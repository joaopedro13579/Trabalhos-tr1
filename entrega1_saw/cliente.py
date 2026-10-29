#!/usr/bin/env python3
"""Cliente RDT-UnB: SAW, Go-Back-N e Selective Repeat com métricas comparáveis."""
import argparse
import csv
import hashlib
import json
import os
import random
import socket
import sys
import time

BUFFER_SIZE = 65507
COLUNAS = [
    "seq", "bytes", "rtt_ms", "tempo_ciclo_ms", "throughput_bps",
    "retransmissoes", "timeout_ocorrido", "perda_simulada", "simulated_loss", "t_rel_s",
]
import csv
from pathlib import Path


def carregar_experimentos_concluidos(caminho_csv):
    concluidos = set()

    if not caminho_csv.exists():
        return concluidos

    with open(caminho_csv, "r", encoding="utf-8", newline="") as f:
        for linha in csv.DictReader(f):
            if linha.get("status", "").strip().upper() != "OK":
                continue

            protocolo = linha["protocolo"].strip().lower()
            janela = int(linha["janela"])
            perda = round(float(linha["perda"]), 4)

            concluidos.add((protocolo, janela, perda))

    return concluidos
class ClienteSAW:
    protocolo = "saw"

    def __init__(self, args):
        self.args = args
        self.servidor = (args.server, args.port)
        self.grupo = args.group
        self.arquivo = args.file
        self.segment_size = args.segment_size
        self.timeout = args.timeout
        self.max_tentativas = args.max_tentativas
        self.pasta_saida = args.out
        self.error = args.error
        self.window = getattr(args, "window", 4)
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.settimeout(self.timeout)

    @staticmethod
    def parse_campos(texto):
        info = {}
        for campo in texto.split("|"):
            if "=" in campo:
                chave, valor = campo.split("=", 1)
                info[chave] = valor
        return info

    @staticmethod
    def separar_data(dados):
        # DATA|campo|campo|<payload binário>: não dividir o payload.
        pos = -1
        for _ in range(3):
            pos = dados.find(b"|", pos + 1)
            if pos == -1:
                raise ValueError("DATA malformado")
        return dados[:pos].decode("ascii", errors="replace"), dados[pos + 1:]

    @staticmethod
    def montar_ack(seq):
        return f"ACK|seq={seq}".encode("utf-8")

    def error_introduction(self):
        return random.random() < self.error

    def handshake(self):
        mensagem = (
            f"HELLO|grupo={self.grupo}|segment_size={self.segment_size}"
            f"|file={self.arquivo}"
        ).encode("utf-8")
        resposta = None
        for tentativa in range(1, self.max_tentativas + 1):
            self.sock.sendto(mensagem, self.servidor)
            prazo = time.perf_counter() + self.timeout
            try:
                while True:
                    restante = prazo - time.perf_counter()
                    if restante <= 0:
                        raise socket.timeout
                    self.sock.settimeout(restante)
                    dados, origem = self.sock.recvfrom(BUFFER_SIZE)
                    if origem != self.servidor:
                        continue
                    texto = dados.decode("utf-8", errors="replace")
                    if texto.startswith("OK|"):
                        resposta = texto
                        break
                    if texto.startswith(("ERR", "ERROR")):
                        raise RuntimeError(f"Servidor recusou HELLO: {texto}")
                break
            except socket.timeout:
                print(f"[HELLO] timeout (tentativa {tentativa})")
        if resposta is None:
            raise RuntimeError("Servidor não respondeu ao HELLO")

        info = self.parse_campos(resposta[3:])
        self.file_size = int(info["file_size"])
        self.checksum_esperado = info["checksum"].lower()
        self.total_segments = int(info["total_segments"])
        self.segment_size = int(info["segment_size"])
        print(f"[HELLO] {resposta}")
        print(f"{self.file_size} bytes, {self.total_segments} segmentos de {self.segment_size} B")

    def iniciar_metricas(self):
        total = self.total_segments
        self.payloads = [None] * total
        self.envios = [0] * total
        self.ultimo_envio = [0.0] * total
        self.primeiro_envio = [0.0] * total
        self.recebimento = [0.0] * total
        self.retransmissoes = [0] * total
        self.perdas = [0] * total
        self.t0 = time.perf_counter()

    def enviar_req(self, seq):
        if self.envios[seq] >= self.max_tentativas:
            raise RuntimeError(f"Limite de tentativas excedido no segmento {seq}")
        agora = time.perf_counter()
        self.sock.sendto(f"REQ|seq={seq}".encode("utf-8"), self.servidor)
        if self.envios[seq] == 0:
            self.primeiro_envio[seq] = agora
        else:
            self.retransmissoes[seq] += 1
        self.envios[seq] += 1
        self.ultimo_envio[seq] = agora

    def receber_data(self, prazo):
        while True:
            restante = prazo - time.perf_counter()
            if restante <= 0:
                raise socket.timeout
            self.sock.settimeout(restante)
            dados, origem = self.sock.recvfrom(BUFFER_SIZE)
            if origem != self.servidor or not dados.startswith(b"DATA|"):
                continue
            try:
                cabecalho, payload = self.separar_data(dados)
                campos = self.parse_campos(cabecalho)
                seq = int(campos["seq"])
            except (ValueError, KeyError):
                continue
            if not 0 <= seq < self.total_segments:
                continue
            return seq, payload

    def aceitar(self, seq, payload):
        if self.error_introduction():
            self.perdas[seq] += 1
            print(f"{self.protocolo.upper()}: DATA {seq} descartado pela simulação")
            return False
        self.payloads[seq] = payload
        self.recebimento[seq] = time.perf_counter()
        self.sock.sendto(self.montar_ack(seq), self.servidor)
        return True

    def transferir(self):
        # Stop-and-Wait: uma solicitação pendente por vez.
        for seq in range(self.total_segments):
            self.enviar_req(seq)
            while self.payloads[seq] is None:
                try:
                    recebido, payload = self.receber_data(
                        self.ultimo_envio[seq] + self.timeout
                    )
                    if recebido != seq:
                        continue
                    self.aceitar(seq, payload)
                except socket.timeout:
                    print(f"SAW: timeout no segmento {seq}")
                    self.enviar_req(seq)
            if seq % 200 == 0 or seq == self.total_segments - 1:
                print(f"SAW: {seq + 1}/{self.total_segments} segmentos")

    def metricas_segmento(self, seq):
        payload = self.payloads[seq]
        rtt = max(0.0, self.recebimento[seq] - self.ultimo_envio[seq])
        ciclo = max(0.0, self.recebimento[seq] - self.primeiro_envio[seq])
        return {
            "seq": seq,
            "bytes": len(payload),
            "rtt_ms": rtt * 1000,
            "tempo_ciclo_ms": ciclo * 1000,
            "throughput_bps": len(payload) * 8 / ciclo if ciclo else 0.0,
            "retransmissoes": self.retransmissoes[seq],
            "timeout_ocorrido": int(self.retransmissoes[seq] > 0),
            "perda_simulada": self.perdas[seq],
            "simulated_loss": int(self.perdas[seq] > 0),
            "t_rel_s": self.recebimento[seq] - self.t0,
        }

    def salvar_resultados(self):
        os.makedirs(self.pasta_saida, exist_ok=True)
        # Arquivos distintos para cada protocolo, tamanho de janela e taxa de erro.
        identificador = (f"{self.protocolo}_{self.arquivo}"
                         f"_w{1 if self.protocolo == 'saw' else self.window}"
                         f"_p{self.error:.3f}")
        caminho_csv = os.path.join(self.pasta_saida, f"metricas_{identificador}.csv")
        caminho_bin = os.path.join(self.pasta_saida, f"recebido_{identificador}.bin")
        caminho_json = os.path.join(self.pasta_saida, f"resumo_{identificador}.json")
        md5 = hashlib.md5()
        total_bytes = 0
        rtts = []
        total_retx = sum(self.retransmissoes)
        total_perdas = sum(self.perdas)
        with open(caminho_csv, "w", newline="", encoding="utf-8") as f_csv, \
                open(caminho_bin, "wb") as f_bin:
            writer = csv.DictWriter(f_csv, fieldnames=COLUNAS)
            writer.writeheader()
            for seq in range(self.total_segments):
                payload = self.payloads[seq]
                if payload is None:
                    raise RuntimeError(f"Segmento {seq} não foi recebido")
                m = self.metricas_segmento(seq)
                f_bin.write(payload)
                md5.update(payload)
                total_bytes += len(payload)
                rtts.append(m["rtt_ms"])
                writer.writerow({
                    k: f"{v:.4f}" if isinstance(v, float) else v
                    for k, v in m.items()
                })

        duracao = time.perf_counter() - self.t0
        checksum_recebido = md5.hexdigest()
        ok = checksum_recebido == self.checksum_esperado
        rtt_medio_ms = sum(rtts) / len(rtts) if rtts else 0.0
        thr_teorico = (self.segment_size * 8) / (rtt_medio_ms / 1000) if rtt_medio_ms else 0.0
        thr_medido = total_bytes * 8 / duracao if duracao else 0.0
        resumo = {
            "grupo": self.grupo,
            "arquivo": self.arquivo,
            "protocolo": self.protocolo,
            "protocol": self.protocolo,
            "window": 1 if self.protocolo == "saw" else self.window,
            "error": self.error,
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
            "rtt_min_ms": min(rtts, default=0.0),
            "rtt_max_ms": max(rtts, default=0.0),
            "retransmissoes_total": total_retx,
            "perdas_simuladas_total": total_perdas,
            "simulated_loss_total": sum(int(n > 0) for n in self.perdas),
            "throughput_teorico_bps": thr_teorico,
            "throughput_medido_bps": thr_medido,
            "eficiencia": thr_medido / thr_teorico if thr_teorico else 0.0,
        }
        with open(caminho_json, "w", encoding="utf-8") as f:
            json.dump(resumo, f, indent=2, ensure_ascii=False)
        print(f"\nProtocolo    : {self.protocolo.upper()}")
        print(f"MD5 esperado : {self.checksum_esperado}")
        print(f"MD5 recebido : {checksum_recebido} -> {'OK' if ok else 'FALHOU'}")
        print(f"Tempo total  : {duracao:.2f} s | retransmissões: {total_retx}")
        print(f"Perdas simuladas: {total_perdas}")
        print(f"RTT médio    : {rtt_medio_ms:.3f} ms")
        print(f"Thr teórico  : {thr_teorico / 1e3:.2f} kbps")
        print(f"Thr medido   : {thr_medido / 1e3:.2f} kbps ({100 * resumo['eficiencia']:.1f}% do teórico)")
        print(f"CSV: {caminho_csv}\nJSON: {caminho_json}\nBIN: {caminho_bin}")
        return ok

    def executar(self):
        try:
            self.handshake()
            self.iniciar_metricas()
            self.transferir()
            return self.salvar_resultados()
        finally:
            self.sock.close()


class ClienteGBN(ClienteSAW):
    protocolo = "gbn"

    def transferir(self):
        total = self.total_segments
        base = 0
        next_seq = 0
        while base < total:
            while next_seq < total and next_seq < base + self.window:
                self.enviar_req(next_seq)
                next_seq += 1
            try:
                seq, payload = self.receber_data(
                    self.ultimo_envio[base] + self.timeout
                )
                if seq != base:
                    # GBN não armazena dados recebidos fora de ordem.
                    continue
                if not self.aceitar(seq, payload):
                    continue
                base += 1
                if base % 200 == 0 or base == total:
                    print(f"GBN: {base}/{total} segmentos")
            except socket.timeout:
                # Reenvia todos os REQs pendentes da janela.
                for seq in range(base, next_seq):
                    self.enviar_req(seq)


class ClienteSR(ClienteSAW):
    protocolo = "sr"

    def transferir(self):
        total = self.total_segments
        base = 0
        next_seq = 0
        while base < total:
            while next_seq < total and next_seq < base + self.window:
                self.enviar_req(next_seq)
                next_seq += 1
            pendentes = [
                seq for seq in range(base, next_seq)
                if self.payloads[seq] is None
            ]
            if not pendentes:
                while base < total and self.payloads[base] is not None:
                    base += 1
                continue
            prazo = min(self.ultimo_envio[seq] + self.timeout for seq in pendentes)
            try:
                seq, payload = self.receber_data(prazo)
                if self.payloads[seq] is not None:
                    # Reconfirma DATA duplicado sem duplicar o payload.
                    self.sock.sendto(self.montar_ack(seq), self.servidor)
                    continue
                if seq < base or seq >= base + self.window:
                    continue
                if not self.aceitar(seq, payload):
                    continue
                while base < total and self.payloads[base] is not None:
                    base += 1
                if base % 200 == 0 or base == total:
                    print(f"SR: {base}/{total} segmentos contíguos")
            except socket.timeout:
                agora = time.perf_counter()
                for seq in pendentes:
                    if self.ultimo_envio[seq] + self.timeout <= agora:
                        self.enviar_req(seq)


def executar_todos(args):
    """Executa os 21 cenários obrigatórios e produz análise comparativa.

    Não prossegue silenciosamente após falhas: cada cenário registra seu estado.
    """
    from copy import copy
    from analisar import Analise_completa
    concluidos=carregar_experimentos_concluidos(Path(args.out) / "status_experimentos.csv")
    cenarios = [("saw", 1)] + [
        (protocolo, janela)
        for protocolo in ("gbn", "sr")
        for janela in (4, 8, 16)
    ]
    perdas = (0.0, 0.05, 0.10)
    resultados = []
    os.makedirs(args.out, exist_ok=True)

    for protocolo, janela in cenarios:
        for perda in perdas:
            if (protocolo, janela, round(perda, 4)) in concluidos:
                print(f"Pulando cenário já concluído: {protocolo.upper()} "
                      f"janela={janela} perda={perda:.0%}")
                resultados.append({"protocolo": protocolo, "janela": janela,
                                   "perda": perda, "status": "OK"})
                continue
            atual = copy(args)
            atual.protocol = protocolo
            atual.window = janela
            atual.error = perda
            print(f"\n{'=' * 60}")
            print(f"Teste {len(resultados)+1}/21: {protocolo.upper()} "
                  f"janela={janela} perda={perda:.0%}")
            print('=' * 60)
            cliente = {"saw": ClienteSAW, "gbn": ClienteGBN,
                       "sr": ClienteSR}[protocolo](atual)
            try:
                ok = cliente.executar()
                estado = "OK" if ok else "MD5_FALHOU"
            except (Exception, KeyboardInterrupt) as erro:
                estado = f"ERRO: {erro}"
                print(f"Falha no cenário: {estado}", file=sys.stderr)
                if isinstance(erro, KeyboardInterrupt):
                    raise
            resultados.append({"protocolo": protocolo, "janela": janela,
                               "perda": perda, "status": estado})
            with open(os.path.join(args.out, "status_experimentos.csv"),
                      "w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=resultados[0].keys())
                writer.writeheader()
                writer.writerows(resultados)

    Analise_completa(args.out)
    falhas = [r for r in resultados if r["status"] != "OK"]
    print(f"\nFinalizado: {21-len(falhas)}/21 cenários com MD5 OK")
    return 0 if not falhas else 2


def main():
    p = argparse.ArgumentParser(
        description="Cliente RDT-UnB: Stop-and-Wait, Go-Back-N e Selective Repeat"
    )
    p.add_argument("--protocol", choices=["saw", "gbn", "sr", "analise", "todos"],
                   default="saw", help="Protocolo: saw, gbn, sr ou analise (default: saw)")
    p.add_argument("--server", default="137.131.178.229")
    p.add_argument("--port", type=int, default=8080)
    p.add_argument("--group", required=True, help="ex.: grupo14")
    p.add_argument("--file", default="small", help="small | medium | large")
    p.add_argument("--segment-size", type=int, default=512)
    p.add_argument("--timeout", type=float, default=1.0,
                   help="Timeout de retransmissão em segundos")
    p.add_argument("--max-tentativas", type=int, default=20)
    p.add_argument("--error", type=float, default=0.0,
                   help="Probabilidade de perda de pacotes (0.0 a 1.0)")
    p.add_argument("--out", default="resultados")
    p.add_argument("--window", type=int, choices=[1, 4, 8, 16], default=4,
                   help="Tamanho da janela para GBN ou SR")
    args = p.parse_args()
    if not 0.0 <= args.error <= 1.0:
        p.error("--error deve estar entre 0.0 e 1.0")
    if args.timeout <= 0 or args.max_tentativas < 1 or args.segment_size < 1:
        p.error("--timeout, --max-tentativas e --segment-size devem ser positivos")
    try:
        if args.protocol == "todos":
            return executar_todos(args)
        if args.protocol == "analise":
            # A análise não precisa abrir uma conexão UDP.
            from analisar import Analise_completa
            Analise_completa(args.out)
            return 0
        classes = {"saw": ClienteSAW, "gbn": ClienteGBN, "sr": ClienteSR}
        cliente = classes[args.protocol](args)
        return 0 if cliente.executar() else 2
    except KeyboardInterrupt:
        print("\nInterrompido.")
        return 130
    except Exception as erro:
        print(f"ERRO: {erro}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())