"""
TR2 / Entrega 1: cliente Stop-and-Wait para o servidor RDT-UnB.

Uso:
    python3 cliente.py --server 137.131.178.229 --group grupo14 --file small

Saídas (na pasta --out, padrão "resultados/"):
    metricas_<file>.csv   métricas por segmento
    resumo_<file>.json    resumo da execução (MD5, médias, throughput teórico)
    recebido_<file>.bin   arquivo reconstruído
"""

import argparse
import csv
import hashlib
import json
import os
import socket
import sys
import time
import random

BUFFER_SIZE = 65507
class ClienteSAW:

    def __init__(self, args):
        self.servidor = (args.server, args.port)
        self.grupo = args.group
        self.arquivo = args.file
        self.segment_size = args.segment_size
        self.timeout = args.timeout
        self.max_tentativas = args.max_tentativas
        self.pasta_saida = args.out
        self.error=args.error
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.settimeout(self.timeout)

    # ------------------------------------------------------------------
    # Utilitários de protocolo
    # ------------------------------------------------------------------

    @staticmethod
    def parse_campos(texto):
        """'chave=valor|chave=valor' -> dict"""
        info = {}
        for campo in texto.split("|"):
            if "=" in campo:
                chave, valor = campo.split("=", 1)
                info[chave] = valor
        return info

    @staticmethod
    def separar_data(dados):
        """
        DATA tem 3 '|' antes do payload: DATA|campo|campo|<payload>.
        O payload é binário e pode conter '|', por isso cortamos
        exatamente no terceiro separador.
        """
        pos = -1
        for _ in range(3):
            pos = dados.find(b"|", pos + 1)
            if pos == -1:
                raise ValueError("DATA malformado")
        cabecalho = dados[:pos].decode("ascii", errors="replace")
        payload = dados[pos + 1:]
        return cabecalho, payload

    @staticmethod
    def montar_ack(seq):
        # Formato do ACK: confira no enunciado do protocolo
        return f"ACK|seq={seq}".encode("utf-8")

    # ------------------------------------------------------------------
    # Handshake HELLO/OK
    # ------------------------------------------------------------------

    def handshake(self):
        mensagem = (
            f"HELLO|grupo={self.grupo}"
            f"|segment_size={self.segment_size}"
            f"|file={self.arquivo}"
        ).encode("utf-8")

        for tentativa in range(1, self.max_tentativas + 1):
            self.sock.sendto(mensagem, self.servidor)
            try:
                while True:
                    dados, _ = self.sock.recvfrom(BUFFER_SIZE)
                    resposta = dados.decode("utf-8", errors="replace")
                    if resposta.startswith("OK|"):
                        break
                    if resposta.startswith(("ERR", "ERROR")):
                        raise RuntimeError(f"Servidor recusou o HELLO: {resposta}")
                    # Qualquer outra coisa (DATA atrasado etc.) é descartada
                break
            except socket.timeout:
                print(f"[HELLO] timeout (tentativa {tentativa})")
        else:
            raise RuntimeError("Servidor não respondeu ao HELLO")

        info = self.parse_campos(resposta[3:])
        self.file_size = int(info["file_size"])
        self.checksum_esperado = info["checksum"].lower()
        self.total_segments = int(info["total_segments"])
        # Negociação: vale o segment_size que o servidor devolveu
        self.segment_size = int(info["segment_size"])

        print(f"[HELLO] {resposta}")
        print(
            f"        {self.file_size} bytes, {self.total_segments} segmentos "
            f"de {self.segment_size} B, MD5 {self.checksum_esperado}"
        )

    # ------------------------------------------------------------------
    # Loop Stop-and-Wait
    # ------------------------------------------------------------------

    def receber_segmento(self, seq):
        """
        Envia REQ e espera o DATA de 'seq'. Em timeout, retransmite o REQ.
        Retorna (payload, metricas).
        """
        req = f"REQ|seq={seq}".encode("utf-8")
        retransmissoes = 0
        t_ciclo = time.perf_counter()

        while True:
            if retransmissoes >= self.max_tentativas:
                raise RuntimeError(
                    f"seq={seq}: {self.max_tentativas} tentativas sem resposta"
                )

            t_envio = time.perf_counter()
            self.sock.sendto(req, self.servidor)
            prazo = t_envio + self.timeout

            try:
                while True:
                    # Timeout conta a partir do envio, não de cada recvfrom,
                    # para que pacotes descartados não estiquem a espera.
                    restante = prazo - time.perf_counter()
                    if restante <= 0:
                        raise socket.timeout
                    self.sock.settimeout(restante)

                    dados, _ = self.sock.recvfrom(BUFFER_SIZE)
                    t_receb = time.perf_counter()

                    if not dados.startswith(b"DATA|"):
                        continue
                    try:
                        cabecalho, payload = self.separar_data(dados)
                    except ValueError:
                        continue

                    campos = self.parse_campos(cabecalho)
                    if "seq" in campos and int(campos["seq"]) != seq:
                        continue

                    if self.error_introduction():
                        print(f"seq={seq}: DATA descartado pela simulação")
                        perda_simulada = 1
                        continue

                    break
                break

            except socket.timeout:
                retransmissoes += 1
                print(f"  seq={seq}: timeout, retransmitindo REQ ({retransmissoes})")

        self.sock.sendto(self.montar_ack(seq), self.servidor)
        t_fim = time.perf_counter()

        rtt = t_receb - t_envio          # RTT da tentativa que deu certo (Karn)
        ciclo = t_fim - t_ciclo          # inclui timeouts e envio do ACK
        bits = len(payload) * 8

        metricas = {
            "seq": seq,
            "bytes": len(payload),
            "rtt_ms": rtt * 1000,
            "tempo_ciclo_ms": ciclo * 1000,
            "throughput_bps": bits / ciclo,
            "retransmissoes": retransmissoes,
            "timeout_ocorrido": int(retransmissoes > 0),
            "t_rel_s": t_fim - self.t0,
        }
        return payload, metricas
    def error_introduction(self):
        if random.random() < self.error:
            return True
        else:
            return False
    def executar(self):
        os.makedirs(self.pasta_saida, exist_ok=True)
        print(f"=== RDT-UnB SAW | {self.servidor[0]}:{self.servidor[1]} "
              f"| {self.grupo} | {self.arquivo} ===")

        self.handshake()

        caminho_csv = os.path.join(self.pasta_saida, f"metricas_{self.arquivo}.csv")
        caminho_bin = os.path.join(self.pasta_saida, f"recebido_{self.arquivo}.bin")

        colunas = ["seq", "bytes", "rtt_ms", "tempo_ciclo_ms", "throughput_bps",
                   "retransmissoes", "timeout_ocorrido", "t_rel_s"]

        md5 = hashlib.md5()
        total_bytes = 0
        rtts = []
        total_retx = 0

        self.t0 = time.perf_counter()

        with open(caminho_csv, "w", newline="") as f_csv, \
             open(caminho_bin, "wb") as f_bin:

            writer = csv.DictWriter(f_csv, fieldnames=colunas)
            writer.writeheader()

            for seq in range(self.total_segments):
                payload, m = self.receber_segmento(seq)

                f_bin.write(payload)
                md5.update(payload)
                total_bytes += len(payload)
                rtts.append(m["rtt_ms"])
                total_retx += m["retransmissoes"]

                writer.writerow({
                    k: (f"{v:.4f}" if isinstance(v, float) else v)
                    for k, v in m.items()
                })

                if seq % 200 == 0 or seq == self.total_segments - 1:
                    print(f"  {seq + 1}/{self.total_segments} "
                          f"RTT={m['rtt_ms']:.2f} ms "
                          f"thr={m['throughput_bps'] / 1e3:.1f} kbps")

        duracao = time.perf_counter() - self.t0
        self.sock.close()

        checksum_recebido = md5.hexdigest()
        ok = checksum_recebido == self.checksum_esperado
        rtt_medio_ms = sum(rtts) / len(rtts)
        thr_teorico = (self.segment_size * 8) / (rtt_medio_ms / 1000)
        thr_medido = total_bytes * 8 / duracao

        resumo = {
            "grupo": self.grupo,
            "arquivo": self.arquivo,
            "file_size": self.file_size,
            "bytes_recebidos": total_bytes,
            "total_segments": self.total_segments,
            "segment_size": self.segment_size,
            "timeout_s": self.timeout,
            "checksum_esperado": self.checksum_esperado,
            "checksum_recebido": checksum_recebido,
            "checksum_ok": ok,
            "duracao_s": duracao,
            "rtt_medio_ms": rtt_medio_ms,
            "rtt_min_ms": min(rtts),
            "rtt_max_ms": max(rtts),
            "retransmissoes_total": total_retx,
            "throughput_teorico_bps": thr_teorico,
            "throughput_medido_bps": thr_medido,
            "eficiencia": thr_medido / thr_teorico,
        }
        with open(os.path.join(self.pasta_saida, f"resumo_{self.arquivo}.json"), "w") as f:
            json.dump(resumo, f, indent=2)

        print()
        print(f"MD5 esperado : {self.checksum_esperado}")
        print(f"MD5 recebido : {checksum_recebido}  -> {'OK' if ok else 'FALHOU'}")
        print(f"Tempo total  : {duracao:.2f} s | retransmissões: {total_retx}")
        print(f"RTT médio    : {rtt_medio_ms:.3f} ms")
        print(f"Thr teórico  : {thr_teorico / 1e3:.2f} kbps")
        print(f"Thr medido   : {thr_medido / 1e3:.2f} kbps "
              f"({100 * thr_medido / thr_teorico:.1f}% do teórico)")

        return ok

class ClienteGBN(ClienteSAW):
    """Cliente Go-Back-N baseado no protocolo REQ/DATA/ACK do ClienteSAW."""

    def __init__(self, args):
        super().__init__(args)
        self.window = getattr(args, "window", 4)
        print("=====================Go back N=====================")           
        if self.window < 1:
            raise ValueError("A janela deve ser maior ou igual a 1")

    def executar_janela(self):
        self.handshake()

        total = self.total_segments
        base = 0
        next_seq = 0

        payloads = [None] * total
        envios = [0] * total
        ultimo_envio = [0.0] * total
        retransmissoes = [0] * total

        while base < total:
            # Preenche a janela de envio.
            while next_seq < total and next_seq < base + self.window:
                req = f"REQ|seq={next_seq}".encode()
                self.sock.sendto(req, self.servidor)

                envios[next_seq] += 1
                ultimo_envio[next_seq] = time.perf_counter()
                next_seq += 1

            # Espera pela resposta do segmento-base.
            restante = (
                ultimo_envio[base] + self.timeout - time.perf_counter()
            )

            try:
                if restante <= 0:
                    raise socket.timeout

                self.sock.settimeout(restante)
                dados, _ = self.sock.recvfrom(65507)

            except socket.timeout:
                # Go-Back-N: reenvia todos os segmentos pendentes.
                for seq in range(base, next_seq):
                    if envios[seq] >= self.max_tentativas:
                        raise RuntimeError(
                            f"Limite de tentativas excedido no segmento {seq}"
                        )

                    req = f"REQ|seq={seq}".encode()
                    self.sock.sendto(req, self.servidor)

                    envios[seq] += 1
                    retransmissoes[seq] += 1
                    ultimo_envio[seq] = time.perf_counter()

                continue

            if not dados.startswith(b"DATA|"):
                continue

            try:
                cabecalho, payload = self.separar_data(dados)
                campos = self.parse_campos(cabecalho)
                seq = int(campos["seq"])
            except (ValueError, KeyError):
                continue


            if seq != base:
                continue

            # Simula a perda do pacote DATA antes de processá-lo.
            if self.error_introduction():
                print(f"GBN: segmento {seq} descartado pela simulação de perda")
                continue

            payloads[seq] = payload

            # Confirma o segmento recebido.
            self.sock.sendto(self.montar_ack(seq), self.servidor)

            print(f"GBN: segmento {seq} recebido")

            # Avança a base da janela.
            base += 1
        return payloads, retransmissoes
class ClienteSR(ClienteSAW):
    """Cliente Selective Repeat baseado no protocolo REQ/DATA/ACK."""

    def __init__(self, args):
        super().__init__(args)
        self.window = getattr(args, "window", 4)
        print("=====================Selective Repete=====================")
        if self.window < 1:
            raise ValueError("A janela deve ser maior ou igual a 1")

    def executar_janela(self):
        self.handshake()

        total = self.total_segments
        base = 0
        next_seq = 0

        payloads = [None] * total
        envios = [0] * total
        ultimo_envio = [0.0] * total
        retransmissoes = [0] * total

        while base < total:
            # Preenche a janela com novas solicitações.
            while next_seq < total and next_seq < base + self.window:
                req = f"REQ|seq={next_seq}".encode()
                self.sock.sendto(req, self.servidor)

                envios[next_seq] += 1
                ultimo_envio[next_seq] = time.perf_counter()
                next_seq += 1

            # Calcula o prazo mais próximo entre os segmentos pendentes.
            pendentes = [
                seq for seq in range(base, next_seq)
                if payloads[seq] is None
            ]

            if not pendentes:
                while base < total and payloads[base] is not None:
                    base += 1
                continue

            prazo = min(
                ultimo_envio[seq] + self.timeout
                for seq in pendentes
            )

            restante = prazo - time.perf_counter()

            try:
                if restante <= 0:
                    raise socket.timeout

                self.sock.settimeout(restante)
                dados, _ = self.sock.recvfrom(65507)

            except socket.timeout:
                agora = time.perf_counter()

                # SR: retransmite somente os segmentos cujo prazo expirou.
                for seq in pendentes:
                    expirou = (
                        ultimo_envio[seq] + self.timeout <= agora
                    )

                    if not expirou:
                        continue

                    if envios[seq] >= self.max_tentativas:
                        raise RuntimeError(
                            f"Limite de tentativas excedido no segmento {seq}"
                        )

                    req = f"REQ|seq={seq}".encode()
                    self.sock.sendto(req, self.servidor)

                    envios[seq] += 1
                    retransmissoes[seq] += 1
                    ultimo_envio[seq] = time.perf_counter()

                continue

            if not dados.startswith(b"DATA|"):
                continue

            try:
                cabecalho, payload = self.separar_data(dados)
                campos = self.parse_campos(cabecalho)
                seq = int(campos["seq"])
            except (ValueError, KeyError):
                continue

            if not 0 <= seq < total:
                continue

            # Segmento já recebido: confirma novamente, sem duplicá-lo.
            if payloads[seq] is not None:
                self.sock.sendto(self.montar_ack(seq), self.servidor)
                continue

            # Aceita segmentos dentro da janela corrente.
            if seq < base or seq >= base + self.window:
                continue

            # Guarda mesmo se o segmento chegar fora de ordem.
            payloads[seq] = payload

            self.sock.sendto(self.montar_ack(seq), self.servidor)

            print(f"SR: segmento {seq} recebido e armazenado")

            # Avança a base apenas quando a sequência estiver completa.
            while base < total and payloads[base] is not None:
                base += 1

        return payloads, retransmissoes

def main():
    p = argparse.ArgumentParser(
        description="Cliente RDT-UnB: Stop-and-Wait, Go-Back-N e Selective Repeat"
    )

    p.add_argument(
        "--protocol",
        choices=["saw", "gbn", "sr"],
        default="saw",
        help="Protocolo: saw, gbn ou sr"
    )
    p.add_argument("--server", default="137.131.178.229")
    p.add_argument("--port", type=int, default=8080)
    p.add_argument("--group", required=True, help="ex.: grupo14")
    p.add_argument("--file", default="small", help="small | medium | large")
    p.add_argument("--segment-size", type=int, default=512)
    p.add_argument(
        "--timeout",
        type=float,
        default=1.0,
        help="Timeout de retransmissão em segundos"
    )
    p.add_argument("--max-tentativas", type=int, default=20)
    p.add_argument("--error", type=float, default=1)
    p.add_argument("--out", default="resultados")
    p.add_argument(
        "--window",
        type=int,
        choices=[1, 4, 8, 16],
        default=4,
        help="Tamanho da janela para GBN ou SR"
    )

    args = p.parse_args()

    try:
        if args.protocol == "gbn":
            cliente = ClienteGBN(args)
        elif args.protocol == "sr":
            cliente = ClienteSR(args)
        else:
            cliente = ClienteSAW(args)

        ok = cliente.executar()

    except KeyboardInterrupt:
        print("\nInterrompido.")
        sys.exit(130)

    except Exception as erro:
        print(f"ERRO: {erro}")
        sys.exit(1)

    sys.exit(0 if ok else 2)



if __name__ == "__main__":
    main()
