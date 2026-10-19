"""
Servidor RDT-UnB falso, só para testar o cliente localmente.
Simula atraso e perda de DATA. NÃO é o servidor da disciplina.

    python3 servidor_teste.py --perda 0.02 --atraso 0.01
    python3 cliente.py --server 127.0.0.1 --port 9090 --group teste --file small
"""

import argparse
import hashlib
import random
import socket
import threading
import time

TAMANHOS = {"small": 1 * 1024 * 1024, "medium": 4 * 1024 * 1024, "large": 8 * 1024 * 1024}


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--port", type=int, default=8080)
    p.add_argument("--perda", type=float, default=0.02)
    p.add_argument("--atraso", type=float, default=0.01, help="atraso base em s")
    p.add_argument("--jitter", type=float, default=0.003)
    args = p.parse_args()

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("127.0.0.1", args.port))
    sessoes = {}
    print(f"Servidor de teste em 127.0.0.1:{args.port}")

    def enviar(pacote, destino):
        atraso = args.atraso + random.expovariate(1 / args.jitter)
        threading.Timer(atraso, sock.sendto, (pacote, destino)).start()

    while True:
        dados, cli = sock.recvfrom(65507)
        if dados == b"PING":
            sock.sendto(f"PONG|time={time.time()}".encode(), cli)
            continue

        texto = dados.decode(errors="replace")
        if texto.startswith("HELLO|"):
            c = dict(x.split("=", 1) for x in texto.split("|")[1:] if "=" in x)
            seed = f"rdtunb_{c['grupo']}_{c['file']}".encode()
            random.seed(seed)
            conteudo = random.randbytes(TAMANHOS.get(c["file"], 1024 * 1024))
            seg = min(int(c["segment_size"]), 1024)
            total = (len(conteudo) + seg - 1) // seg
            sessoes[cli] = (conteudo, seg)
            random.seed()
            sock.sendto(
                f"OK|file_size={len(conteudo)}|checksum={hashlib.md5(conteudo).hexdigest()}"
                f"|total_segments={total}|segment_size={seg}".encode(), cli)

        elif texto.startswith("REQ|") and cli in sessoes:
            conteudo, seg = sessoes[cli]
            n = int(texto.split("seq=")[1])
            payload = conteudo[n * seg:(n + 1) * seg]
            if random.random() < args.perda:
                continue  # DATA "perdido"
            cab = f"DATA|seq={n}|len={len(payload)}|".encode()
            enviar(cab + payload, cli)

        # ACK é só recebido e ignorado


if __name__ == "__main__":
    main()
