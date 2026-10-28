
"""
Gera gráficos por segmento e tabelas comparativas para SAW, GBN e SR.

Uso:
    python3 analisar.py
    python3 analisar.py --dir outra_pasta

Os arquivos de métricas e resumos devem ser gerados pelo cliente.py.
O protocolo pode ser identificado pelo campo "protocol"/"protocolo"
no JSON ou pelo prefixo do nome do arquivo.
"""

import argparse
import csv
import glob
import json
import os

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt


AZUL = "#2a78d6"
AZUL_CLARO = "#9ec5f4"
VERMELHO = "#d03b3b"
VERDE = "#27864b"
TINTA = "#0b0b0b"
TINTA_2 = "#52514e"
GRADE = "#e4e3df"

ORDEM = ["small", "medium", "large"]
PROTOCOLOS = ["saw", "gbn", "sr"]


def identificar_protocolo(nome, resumo):
    """Identifica o protocolo utilizado na transferência."""
    protocolo = resumo.get("protocol", resumo.get("protocolo", ""))

    if protocolo:
        protocolo = str(protocolo).lower().strip()
        if protocolo in PROTOCOLOS:
            return protocolo

    nome_lower = nome.lower()

    for protocolo in PROTOCOLOS:
        if nome_lower.startswith(protocolo + "_"):
            return protocolo

    # Compatibilidade com os resultados antigos, que eram somente SAW.
    return "saw"


def ler_csv(caminho):
    """Lê métricas por segmento, incluindo perda simulada se disponível."""
    with open(caminho, newline="", encoding="utf-8") as f:
        linhas = list(csv.DictReader(f))

    if not linhas:
        raise ValueError(f"O CSV está vazio: {caminho}")

    def valor(linha, campo, padrao=0.0, tipo=float):
        dado = linha.get(campo, "")
        if dado is None or str(dado).strip() == "":
            return tipo(padrao)
        return tipo(dado)

    return {
        "seq": [
            valor(l, "seq", tipo=int) for l in linhas
        ],
        "rtt_ms": [
            valor(l, "rtt_ms") for l in linhas
        ],
        "thr_kbps": [
            valor(l, "throughput_bps") / 1e3 for l in linhas
        ],
        "timeout": [
            valor(l, "timeout_ocorrido", tipo=int) for l in linhas
        ],
        "retx": [
            valor(l, "retransmissoes", tipo=int) for l in linhas
        ],
        "perda_simulada": [
            valor(l, "perda_simulada", tipo=int) for l in linhas
        ],
    }


def media_movel(valores, janela):
    """Calcula a média móvel de uma lista."""
    if not valores:
        return []

    janela = max(1, min(janela, len(valores)))
    saida = []
    soma = 0.0

    for i, v in enumerate(valores):
        soma += v

        if i >= janela:
            soma -= valores[i - janela]

        saida.append(soma / min(i + 1, janela))

    return saida


def estilo(ax):
    """Aplica estilo aos eixos do gráfico."""
    for lado in ("top", "right"):
        ax.spines[lado].set_visible(False)

    for lado in ("left", "bottom"):
        ax.spines[lado].set_color(TINTA_2)

    ax.tick_params(colors=TINTA_2, labelsize=8)
    ax.grid(axis="y", color=GRADE, linewidth=0.8)
    ax.set_axisbelow(True)


def grafico(arquivo, d, resumo, pasta, protocolo):
    """Gera gráfico de throughput e RTT por segmento."""
    seq = d["seq"]

    if not seq:
        raise ValueError(f"Não há segmentos para gerar o gráfico de {arquivo}")

    janela = max(10, len(seq) // 100)

    fig, (ax1, ax2) = plt.subplots(
        2,
        1,
        figsize=(8, 5.8),
        sharex=True,
        gridspec_kw={"hspace": 0.2},
    )

    fig.patch.set_facecolor("#fcfcfb")

    # Throughput por segmento
    ax1.plot(
        seq,
        d["thr_kbps"],
        color=AZUL_CLARO,
        linewidth=0.7,
        label="Por segmento",
    )

    ax1.plot(
        seq,
        media_movel(d["thr_kbps"], janela),
        color=AZUL,
        linewidth=2,
        label=f"Média móvel ({janela} seg.)",
    )

    thr_teo = float(resumo.get("throughput_teorico_bps", 0)) / 1e3

    if thr_teo > 0:
        ax1.axhline(
            thr_teo,
            color=TINTA_2,
            linewidth=1,
            linestyle="--",
            label=f"Teórico: {thr_teo:.2f} kbps",
        )

    ax1.set_ylabel("Throughput (kbps)", fontsize=9, color=TINTA)
    ax1.set_ylim(bottom=0)

    # RTT por segmento
    ax2.plot(
        seq,
        d["rtt_ms"],
        color=AZUL_CLARO,
        linewidth=0.7,
        label="RTT por segmento",
    )

    ax2.plot(
        seq,
        media_movel(d["rtt_ms"], janela),
        color=AZUL,
        linewidth=2,
        label="Média móvel",
    )

    rtt_medio = float(resumo.get("rtt_medio_ms", 0))

    if rtt_medio > 0:
        ax2.axhline(
            rtt_medio,
            color=TINTA_2,
            linewidth=1,
            linestyle="--",
            label=f"Média geral: {rtt_medio:.2f} ms",
        )

    ax2.set_ylabel("RTT (ms)", fontsize=9, color=TINTA)
    ax2.set_xlabel("Número de sequência do segmento", fontsize=9, color=TINTA)
    ax2.set_ylim(bottom=0)

    # Marca segmentos que tiveram timeout
    indice = {s: i for i, s in enumerate(seq)}

    seq_timeout = [
        s for s, t in zip(seq, d["timeout"]) if t
    ]

    if seq_timeout:
        thr_timeout = [
            d["thr_kbps"][indice[s]] for s in seq_timeout
        ]

        rtt_timeout = [
            d["rtt_ms"][indice[s]] for s in seq_timeout
        ]

        ax1.scatter(
            seq_timeout,
            thr_timeout,
            s=22,
            color=VERMELHO,
            zorder=3,
            edgecolors="#fcfcfb",
            linewidths=0.7,
            label=f"Timeout ({len(seq_timeout)} seg.)",
        )

        ax2.scatter(
            seq_timeout,
            rtt_timeout,
            s=22,
            color=VERMELHO,
            zorder=3,
            edgecolors="#fcfcfb",
            linewidths=0.7,
        )

    # Marca perdas simuladas, se registradas no CSV
    seq_perda = [
        s for s, perda in zip(seq, d["perda_simulada"]) if perda
    ]

    if seq_perda:
        thr_perda = [
            d["thr_kbps"][indice[s]] for s in seq_perda
        ]

        rtt_perda = [
            d["rtt_ms"][indice[s]] for s in seq_perda
        ]

        ax1.scatter(
            seq_perda,
            thr_perda,
            s=28,
            marker="x",
            color=VERDE,
            zorder=4,
            label=f"Perda simulada ({len(seq_perda)} seg.)",
        )

        ax2.scatter(
            seq_perda,
            rtt_perda,
            s=28,
            marker="x",
            color=VERDE,
            zorder=4,
        )

    for ax in (ax1, ax2):
        estilo(ax)
        ax.set_facecolor("#fcfcfb")

    ax1.legend(
        loc="lower left",
        bbox_to_anchor=(0, 1.0),
        fontsize=7,
        frameon=False,
        ncol=2,
    )

    fig.suptitle(
        f"{protocolo.upper()}: throughput e RTT por segmento "
        f"({arquivo}, {resumo.get('segment_size', '?')} B/seg, "
        f"{len(seq)} segmentos)",
        fontsize=10,
        color=TINTA,
        x=0.125,
        y=0.99,
        ha="left",
    )

    caminho = os.path.join(
        pasta,
        f"grafico_{protocolo}_{arquivo}.png",
    )

    fig.savefig(caminho, dpi=200, bbox_inches="tight")
    plt.close(fig)

    return caminho


def carregar_resumos(pasta):
    """Carrega os resumos JSON encontrados na pasta."""
    resumos = {}

    padrao = os.path.join(pasta, "resumo_*.json")

    for caminho in glob.glob(padrao):
        try:
            with open(caminho, encoding="utf-8") as f:
                resumo = json.load(f)
        except (OSError, json.JSONDecodeError) as erro:
            print(f"Aviso: não foi possível ler {caminho}: {erro}")
            continue

        nome = resumo.get("arquivo")

        if not nome:
            # Compatibilidade com nomes como resumo_saw_small.json.
            base = os.path.basename(caminho)
            nome = base[len("resumo_"):-len(".json")]

        resumos[nome] = resumo

    return resumos


def main():
    parser = argparse.ArgumentParser(
        description=(
            "Gera gráficos e tabelas comparativas para SAW, GBN e SR."
        )
    )

    parser.add_argument(
        "--dir",
        default="resultados",
        help="Pasta que contém os CSVs e JSONs.",
    )

    parser.add_argument(
        "--protocol",
        choices=PROTOCOLOS,
        default=None,
        help="Analisa somente um protocolo específico.",
    )

    args = parser.parse_args()
    pasta = args.dir

    if not os.path.isdir(pasta):
        print(f"Erro: a pasta '{pasta}' não existe.")
        return

    resumos = carregar_resumos(pasta)

    if not resumos:
        print(f"Nenhum resumo JSON encontrado em '{pasta}/'.")
        print("Execute o cliente.py antes de analisar os resultados.")
        return

    linhas = []

    for nome, resumo in resumos.items():
        protocolo = identificar_protocolo(nome, resumo)

        if args.protocol and protocolo != args.protocol:
            continue

        # Aceita dois padrões de nome:
        # metricas_small.csv
        # metricas_saw_small.csv
        candidatos = [
            os.path.join(pasta, f"metricas_{nome}.csv"),
        ]

        if not nome.startswith(protocolo + "_"):
            candidatos.append(
                os.path.join(pasta, f"metricas_{protocolo}_{nome}.csv")
            )

        caminho_csv = next(
            (c for c in candidatos if os.path.isfile(c)),
            None,
        )

        if caminho_csv is None:
            print(
                f"Aviso: CSV não encontrado para '{nome}' "
                f"(protocolo {protocolo.upper()})."
            )
            continue

        try:
            d = ler_csv(caminho_csv)
        except (OSError, ValueError, csv.Error) as erro:
            print(f"Aviso: erro ao ler {caminho_csv}: {erro}")
            continue

        png = grafico(nome, d, resumo, pasta, protocolo)
        print(f"Gráfico: {png}")

        media_seg = sum(d["thr_kbps"]) / len(d["thr_kbps"])

        thr_teorico = float(
            resumo.get("throughput_teorico_bps", 0)
        )

        thr_medido = float(
            resumo.get("throughput_medido_bps", 0)
        )

        eficiencia = resumo.get("eficiencia")

        if eficiencia is None:
            eficiencia = (
                thr_medido / thr_teorico
                if thr_teorico > 0
                else 0
            )

        linhas.append({
            "protocolo": protocolo.upper(),
            "arquivo": nome,
            "segmentos": resumo.get("total_segments", len(d["seq"])),
            "rtt_medio_ms": (
                f"{float(resumo.get('rtt_medio_ms', 0)):.2f}"
            ),
            "thr_teorico_kbps": f"{thr_teorico / 1e3:.2f}",
            "thr_medido_kbps": f"{thr_medido / 1e3:.2f}",
            "thr_medio_por_seg_kbps": f"{media_seg:.2f}",
            "eficiencia_pct": f"{100 * float(eficiencia):.1f}",
            "retransmissoes": resumo.get(
                "retransmissoes_total",
                sum(d["retx"]),
            ),
            "timeouts_csv": sum(1 for t in d["timeout"] if t),
            "perdas_simuladas_csv": sum(d["perda_simulada"]),
            "duracao_s": f"{float(resumo.get('duracao_s', 0)):.2f}",
            "md5": resumo.get("checksum_recebido", ""),
            "md5_ok": (
                "sim" if resumo.get("checksum_ok") else "NÃO"
            ),
        })

    if not linhas:
        print("Nenhum conjunto de resultados pôde ser analisado.")
        print(
            "Confira se os nomes dos arquivos CSV correspondem "
            "aos nomes dos resumos JSON."
        )
        return

    # Tabela comparativa CSV
    caminho_csv = os.path.join(pasta, "tabela_comparativa.csv")

    with open(caminho_csv, "w", newline="", encoding="utf-8") as f:
        escritor = csv.DictWriter(
            f,
            fieldnames=list(linhas[0].keys()),
        )
        escritor.writeheader()
        escritor.writerows(linhas)

    # Tabela Markdown para o relatório
    cabecalhos = [
        "Protocolo",
        "Arquivo",
        "Seg.",
        "RTT médio (ms)",
        "Teórico (kbps)",
        "Medido (kbps)",
        "Média por seg. (kbps)",
        "Eficiência",
        "Retx.",
        "Timeouts",
        "Perdas simuladas",
        "Tempo (s)",
        "MD5 OK",
    ]

    markdown = [
        "| " + " | ".join(cabecalhos) + " |",
        "|" + "|".join(["---"] * len(cabecalhos)) + "|",
    ]

    for linha in linhas:
        markdown.append("| " + " | ".join([
            linha["protocolo"],
            str(linha["arquivo"]),
            str(linha["segmentos"]),
            linha["rtt_medio_ms"],
            linha["thr_teorico_kbps"],
            linha["thr_medido_kbps"],
            linha["thr_medio_por_seg_kbps"],
            linha["eficiencia_pct"] + "%",
            str(linha["retransmissoes"]),
            str(linha["timeouts_csv"]),
            str(linha["perdas_simuladas_csv"]),
            linha["duracao_s"],
            linha["md5_ok"],
        ]) + " |")

    markdown.extend(["", "MD5 recebido:"])

    for linha in linhas:
        markdown.append(
            f"- {linha['protocolo']} / {linha['arquivo']}: "
            f"`{linha['md5']}`"
        )

    caminho_md = os.path.join(pasta, "tabela_comparativa.md")

    with open(caminho_md, "w", encoding="utf-8") as f:
        f.write("\n".join(markdown) + "\n")

    print(f"Tabela CSV: {caminho_csv}")
    print(f"Tabela Markdown: {caminho_md}")
    print()
    print("\n".join(markdown))


if __name__ == "__main__":
    main()
