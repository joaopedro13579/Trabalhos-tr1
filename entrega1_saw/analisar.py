"""
Gera os gráficos por segmento e a tabela teórico x medido
a partir do que o cliente.py salvou em resultados/.

Uso:
    python3 analisar.py                # usa resultados/
    python3 analisar.py --dir outra_pasta
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
TINTA = "#0b0b0b"
TINTA_2 = "#52514e"
GRADE = "#e4e3df"

ORDEM = ["small", "medium", "large"]


def ler_csv(caminho):
    with open(caminho) as f:
        linhas = list(csv.DictReader(f))
    return {
        "seq": [int(l["seq"]) for l in linhas],
        "rtt_ms": [float(l["rtt_ms"]) for l in linhas],
        "thr_kbps": [float(l["throughput_bps"]) / 1e3 for l in linhas],
        "timeout": [int(l["timeout_ocorrido"]) for l in linhas],
        "retx": [int(l["retransmissoes"]) for l in linhas],
    }


def media_movel(valores, janela):
    janela = max(1, min(janela, len(valores)))
    saida, soma = [], 0.0
    for i, v in enumerate(valores):
        soma += v
        if i >= janela:
            soma -= valores[i - janela]
        saida.append(soma / min(i + 1, janela))
    return saida


def estilo(ax):
    for lado in ("top", "right"):
        ax.spines[lado].set_visible(False)
    for lado in ("left", "bottom"):
        ax.spines[lado].set_color(TINTA_2)
    ax.tick_params(colors=TINTA_2, labelsize=8)
    ax.grid(axis="y", color=GRADE, linewidth=0.8)
    ax.set_axisbelow(True)


def grafico(arquivo, d, resumo, pasta):
    seq = d["seq"]
    janela = max(10, len(seq) // 100)

    fig, (ax1, ax2) = plt.subplots(
        2, 1, figsize=(8, 5.6), sharex=True,
        gridspec_kw={"hspace": 0.18},
    )
    fig.patch.set_facecolor("#fcfcfb")

    # --- Throughput por segmento ---
    ax1.plot(seq, d["thr_kbps"], color=AZUL_CLARO, linewidth=0.6,
             label="por segmento")
    ax1.plot(seq, media_movel(d["thr_kbps"], janela), color=AZUL,
             linewidth=2, label=f"média móvel ({janela} seg.)")
    thr_teo = resumo["throughput_teorico_bps"] / 1e3
    ax1.axhline(thr_teo, color=TINTA_2, linewidth=1, linestyle="--")
    ax1.annotate(f"teórico {thr_teo:.0f} kbps", (seq[-1], thr_teo),
                 xytext=(0, 4), textcoords="offset points",
                 va="bottom", ha="right", fontsize=8, color=TINTA_2,
                 bbox=dict(fc="#fcfcfb", ec="none", pad=1.5, alpha=0.9))
    ax1.set_ylabel("Throughput (kbps)", fontsize=9, color=TINTA)
    ax1.set_ylim(bottom=0)

    # --- RTT por segmento ---
    ax2.plot(seq, d["rtt_ms"], color=AZUL_CLARO, linewidth=0.6)
    ax2.plot(seq, media_movel(d["rtt_ms"], janela), color=AZUL, linewidth=2)
    rtt_med = resumo["rtt_medio_ms"]
    ax2.axhline(rtt_med, color=TINTA_2, linewidth=1, linestyle="--")
    ax2.annotate(f"média {rtt_med:.1f} ms", (seq[-1], rtt_med),
                 xytext=(0, 4), textcoords="offset points",
                 va="bottom", ha="right", fontsize=8, color=TINTA_2,
                 bbox=dict(fc="#fcfcfb", ec="none", pad=1.5, alpha=0.9))
    ax2.set_ylabel("RTT (ms)", fontsize=9, color=TINTA)
    ax2.set_xlabel("Número de sequência do segmento", fontsize=9, color=TINTA)
    ax2.set_ylim(bottom=0)

    # Segmentos com timeout: marcados nos dois painéis
    ts = [s for s, t in zip(seq, d["timeout"]) if t]
    if ts:
        thr_ts = [d["thr_kbps"][s] for s in ts]
        rtt_ts = [d["rtt_ms"][s] for s in ts]
        ax1.scatter(ts, thr_ts, s=22, color=VERMELHO, zorder=3,
                    edgecolors="#fcfcfb", linewidths=1,
                    label=f"timeout ({len(ts)} seg.)")
        ax2.scatter(ts, rtt_ts, s=22, color=VERMELHO, zorder=3,
                    edgecolors="#fcfcfb", linewidths=1)

    for ax in (ax1, ax2):
        estilo(ax)
        ax.set_facecolor("#fcfcfb")

    ax1.legend(loc="lower left", bbox_to_anchor=(0, 1.0), fontsize=8,
               frameon=False, ncol=3)
    fig.suptitle(
        f"SAW: throughput e RTT por segmento ({arquivo}, "
        f"{resumo['segment_size']} B/seg, {len(seq)} segmentos)",
        fontsize=10, color=TINTA, x=0.125, y=0.99, ha="left",
    )

    caminho = os.path.join(pasta, f"grafico_{arquivo}.png")
    fig.savefig(caminho, dpi=200, bbox_inches="tight")
    plt.close(fig)
    return caminho


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--dir", default="resultados")
    args = p.parse_args()

    resumos = {}
    for caminho in glob.glob(os.path.join(args.dir, "resumo_*.json")):
        with open(caminho) as f:
            r = json.load(f)
        resumos[r["arquivo"]] = r

    if not resumos:
        print(f"Nenhum resumo_*.json em {args.dir}/. Rode o cliente.py antes.")
        return

    nomes = sorted(resumos, key=lambda n: ORDEM.index(n) if n in ORDEM else 99)
    linhas = []

    for nome in nomes:
        r = resumos[nome]
        d = ler_csv(os.path.join(args.dir, f"metricas_{nome}.csv"))
        png = grafico(nome, d, r, args.dir)

        media_seg = sum(d["thr_kbps"]) / len(d["thr_kbps"])
        linhas.append({
            "arquivo": nome,
            "segmentos": r["total_segments"],
            "rtt_medio_ms": f"{r['rtt_medio_ms']:.2f}",
            "thr_teorico_kbps": f"{r['throughput_teorico_bps'] / 1e3:.2f}",
            "thr_medido_kbps": f"{r['throughput_medido_bps'] / 1e3:.2f}",
            "thr_medio_por_seg_kbps": f"{media_seg:.2f}",
            "eficiencia_pct": f"{100 * r['eficiencia']:.1f}",
            "retransmissoes": r["retransmissoes_total"],
            "duracao_s": f"{r['duracao_s']:.2f}",
            "md5": r["checksum_recebido"],
            "md5_ok": "sim" if r["checksum_ok"] else "NÃO",
        })
        print(f"Gráfico: {png}")

    # Tabela em CSV
    caminho_csv = os.path.join(args.dir, "tabela_comparativa.csv")
    with open(caminho_csv, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(linhas[0]))
        w.writeheader()
        w.writerows(linhas)

    # Tabela em Markdown (para colar no relatório)
    cab = ["Arquivo", "Seg.", "RTT médio (ms)", "Teórico (kbps)",
           "Medido (kbps)", "Medido/Teórico", "Retx.", "Tempo (s)", "MD5 OK"]
    md = ["| " + " | ".join(cab) + " |",
          "|" + "|".join(["---"] * len(cab)) + "|"]
    for l in linhas:
        md.append("| " + " | ".join([
            l["arquivo"], str(l["segmentos"]), l["rtt_medio_ms"],
            l["thr_teorico_kbps"], l["thr_medido_kbps"],
            l["eficiencia_pct"] + "%", str(l["retransmissoes"]),
            l["duracao_s"], l["md5_ok"],
        ]) + " |")
    md.append("")
    md.append("MD5 recebido:")
    for l in linhas:
        md.append(f"- {l['arquivo']}: `{l['md5']}`")

    caminho_md = os.path.join(args.dir, "tabela_comparativa.md")
    with open(caminho_md, "w") as f:
        f.write("\n".join(md) + "\n")

    print(f"Tabela: {caminho_csv}")
    print(f"Tabela: {caminho_md}")
    print()
    print("\n".join(md))


if __name__ == "__main__":
    main()
