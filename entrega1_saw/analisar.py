#!/usr/bin/env python3
"""Análise comparativa dos resultados SAW, GBN e SR do cliente.py.

Uso: python3 analisar.py [--dir resultados] [--protocol saw|gbn|sr]
API: Analise_completa(pasta='resultados', protocolo=None)
"""
import argparse
import csv
import glob
import json
import os
from collections import defaultdict

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

PROTOCOLOS = ('saw', 'gbn', 'sr')
CORES = {'saw': '#277da1', 'gbn': '#f8961e', 'sr': '#43aa8b'}
COLUNAS = [
    'protocolo', 'arquivo', 'janela', 'erro_configurado', 'segmentos',
    'rtt_medio_ms', 'thr_referencia_saw_kbps', 'thr_medido_kbps',
    'ganho_vs_saw', 'retransmissoes', 'timeouts_segmentos',
    'perdas_simuladas', 'duracao_s', 'bytes_recebidos', 'md5_ok', 'md5',
]


def numero(valor, padrao=0.0):
    try:
        return float(valor)
    except (ValueError, TypeError):
        return float(padrao)


def ler_csv(caminho):
    with open(caminho, newline='', encoding='utf-8') as f:
        linhas = list(csv.DictReader(f))
    if not linhas:
        raise ValueError(f'CSV vazio: {caminho}')
    linhas.sort(key=lambda r: int(numero(r.get('seq'))))
    return {
        'seq': [int(numero(r.get('seq'))) for r in linhas],
        'rtt_ms': [numero(r.get('rtt_ms')) for r in linhas],
        'thr_kbps': [numero(r.get('throughput_bps')) / 1000 for r in linhas],
        'retx': [int(numero(r.get('retransmissoes'))) for r in linhas],
        'timeout': [int(numero(r.get('timeout_ocorrido'))) for r in linhas],
        'perda_simulada': [int(numero(r.get('perda_simulada', r.get('simulated_loss')))) for r in linhas],
        'tempo_ciclo_ms': [numero(r.get('tempo_ciclo_ms')) for r in linhas],
    }


def media_movel(valores, janela):
    janela = max(1, int(janela))
    resultado = []
    acumulado = 0.0
    for i, valor in enumerate(valores):
        acumulado += valor
        if i >= janela:
            acumulado -= valores[i - janela]
        resultado.append(acumulado / min(i + 1, janela))
    return resultado


def identificar_protocolo(caminho, resumo):
    p = str(resumo.get('protocolo') or resumo.get('protocol') or '').lower()
    if p in PROTOCOLOS:
        return p
    nome = os.path.basename(caminho).lower()
    for candidato in PROTOCOLOS:
        if nome.startswith(f'resumo_{candidato}_'):
            return candidato
    return 'saw'  # arquivos legados sem identificação


def localizar_csv(pasta, caminho_json, protocolo, arquivo):
    base = os.path.basename(caminho_json)
    sufixo = base[len('resumo_'):-len('.json')]
    candidatos = [
        os.path.join(pasta, f'metricas_{sufixo}.csv'),
        os.path.join(pasta, f'metricas_{protocolo}_{arquivo}.csv'),
        os.path.join(pasta, f'metricas_{arquivo}.csv'),
    ]
    return next((c for c in candidatos if os.path.isfile(c)), None)


def grafico(arquivo, dados, resumo, pasta, protocolo):
    seq = dados['seq']
    janela_media = max(5, len(seq) // 100)
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(10, 7), sharex=True)
    cor = CORES[protocolo]
    for ax, chave, titulo, unidade in (
        (ax1, 'thr_kbps', 'Throughput por segmento', 'kbps'),
        (ax2, 'rtt_ms', 'RTT por segmento', 'ms'),
    ):
        valores = dados[chave]
        ax.plot(seq, valores, color=cor, alpha=0.3, lw=0.7, label='Por segmento')
        ax.plot(seq, media_movel(valores, janela_media), color=cor, lw=1.8,
                label=f'Média móvel ({janela_media})')
        indices_timeout = [i for i, x in enumerate(dados['timeout']) if x]
        if indices_timeout:
            ax.scatter([seq[i] for i in indices_timeout],
                       [valores[i] for i in indices_timeout],
                       s=15, c='#d62828', label='Timeout', zorder=3)
        indices_perda = [i for i, x in enumerate(dados['perda_simulada']) if x]
        if indices_perda:
            ax.scatter([seq[i] for i in indices_perda],
                       [valores[i] for i in indices_perda],
                       s=20, marker='x', c='#6a4c93', label='Perda simulada', zorder=4)
        ax.set_title(titulo)
        ax.set_ylabel(unidade)
        ax.grid(alpha=0.25)
        ax.legend(fontsize=8)
    ax2.set_xlabel('Número de sequência')
    fig.suptitle(f'{protocolo.upper()} | {arquivo} | janela={resumo.get("window", 1)} '
                 f'| erro={numero(resumo.get("error")):.1%}')
    fig.tight_layout()
    nome = os.path.basename(arquivo).replace('/', '_').replace('\\', '_')
    janela = resumo.get('window', 1)
    erro = numero(resumo.get('error'))
    caminho = os.path.join(pasta, f'grafico_{protocolo}_{nome}_w{janela}_p{erro:.3f}.png')
    fig.savefig(caminho, dpi=160, bbox_inches='tight')
    plt.close(fig)
    return caminho


def grafico_comparativo(linhas, pasta, campo, ylabel, nome):
    """Compara execuções sem misturar tamanhos de janela diferentes."""
    grupos = sorted({(l['arquivo'], l['erro_configurado'], str(l['janela']))
                    for l in linhas})
    if not grupos:
        return None
    fig, ax = plt.subplots(figsize=(max(10, len(grupos) * 1.65), 5.5))
    largura = 0.24
    desenhou = False
    for j, protocolo in enumerate(PROTOCOLOS):
        posicoes, valores = [], []
        for i, (arquivo, erro, janela) in enumerate(grupos):
            # SAW (janela 1) é referência para todas as janelas do mesmo teste.
            candidatos = [l for l in linhas if l['arquivo'] == arquivo
                          and l['erro_configurado'] == erro
                          and l['protocolo'] == protocolo
                          and (protocolo == 'saw' or str(l['janela']) == janela)]
            if not candidatos:
                continue
            valores_validos = [numero(l[campo]) for l in candidatos if l[campo] != '']
            if not valores_validos:
                continue
            posicoes.append(i + (j - 1) * largura)
            valores.append(sum(valores_validos) / len(valores_validos))
        if valores:
            ax.bar(posicoes, valores, largura, color=CORES[protocolo], label=protocolo.upper())
            desenhou = True
    if not desenhou:
        plt.close(fig)
        return None
    ax.set_xticks(range(len(grupos)), [f'{a}\nperda={e:.0%} | janela={w}' for a,e,w in grupos])
    ax.set_ylabel(ylabel)
    ax.set_title(f'SAW × GBN × SR — {ylabel}')
    ax.grid(axis='y', alpha=0.2)
    ax.set_axisbelow(True)
    ax.legend()
    fig.tight_layout()
    caminho = os.path.join(pasta, nome)
    fig.savefig(caminho, dpi=180, bbox_inches='tight')
    plt.close(fig)
    return caminho


def grafico_sobreposto(registros, pasta, arquivo, erro, janela, campo, titulo, unidade):
    fig, ax = plt.subplots(figsize=(10, 5.5))
    for protocolo in PROTOCOLOS:
        candidatos = [r for r in registros if r['protocolo'] == protocolo
                      and r['arquivo'] == arquivo and r['erro'] == erro
                      and (protocolo == 'saw' or str(r['janela']) == str(janela))]
        if not candidatos:
            continue
        dados = candidatos[0]['dados']
        ax.plot(dados['seq'], media_movel(dados[campo], max(5, len(dados['seq']) // 100)),
                label=protocolo.upper(), color=CORES[protocolo], linewidth=1.6)
    if not ax.lines:
        plt.close(fig)
        return None
    ax.set_xlabel('Número do segmento')
    ax.set_ylabel(unidade)
    ax.set_title(f'{titulo} — {arquivo} | perda={erro:.0%} | janela={janela}')
    ax.grid(alpha=0.25)
    ax.legend()
    fig.tight_layout()
    arquivo_seguro = ''.join(c if c.isalnum() or c in '-_' else '_' for c in arquivo)
    caminho = os.path.join(pasta, f'comparacao_segmentos_{campo}_{arquivo_seguro}_p{erro:.3f}_w{janela}.png')
    fig.savefig(caminho, dpi=180, bbox_inches='tight')
    plt.close(fig)
    return caminho


def salvar_tabelas(linhas, pasta):
    csv_saida = os.path.join(pasta, 'tabela_comparativa.csv')
    with open(csv_saida, 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=COLUNAS)
        writer.writeheader()
        writer.writerows(linhas)
    md_saida = os.path.join(pasta, 'tabela_comparativa.md')
    with open(md_saida, 'w', encoding='utf-8') as f:
        f.write('| ' + ' | '.join(COLUNAS) + ' |\n')
        f.write('| ' + ' | '.join(['---'] * len(COLUNAS)) + ' |\n')
        for linha in linhas:
            f.write('| ' + ' | '.join(str(linha[c]) for c in COLUNAS) + ' |\n')
    return csv_saida, md_saida


def Analise_completa(pasta='resultados', protocolo=None):
    """Analisa todos os protocolos, gera gráficos e tabelas e devolve caminhos.

    Pode ser chamada por cliente.py com Analise_completa(args.out).
    """
    if protocolo is not None and protocolo not in PROTOCOLOS:
        raise ValueError(f'Protocolo inválido: {protocolo}')
    if not os.path.isdir(pasta):
        raise FileNotFoundError(f'Pasta não encontrada: {pasta}')
    saida = {'saw': [], 'gbn': [], 'sr': [], 'comparativos': [], 'tabelas': []}
    linhas = []
    registros = []
    for caminho_json in sorted(glob.glob(os.path.join(pasta, 'resumo_*.json'))):
        try:
            with open(caminho_json, encoding='utf-8') as f:
                resumo = json.load(f)
            p = identificar_protocolo(caminho_json, resumo)
            if protocolo is not None and p != protocolo:
                continue
            arquivo = str(resumo.get('arquivo') or os.path.basename(caminho_json)[7:-5])
            caminho_csv = localizar_csv(pasta, caminho_json, p, arquivo)
            if not caminho_csv:
                print(f'Aviso: CSV não encontrado para {caminho_json}')
                continue
            dados = ler_csv(caminho_csv)
            registros.append({'protocolo': p, 'arquivo': arquivo,
                              'erro': numero(resumo.get('error')),
                              'janela': resumo.get('window', 1 if p == 'saw' else '?'),
                              'dados': dados})
            graf = grafico(arquivo, dados, resumo, pasta, p)
            saida[p].append(graf)
            thr_medido = numero(resumo.get('throughput_medido_bps')) / 1000
            thr_ref = numero(resumo.get('throughput_teorico_bps')) / 1000
            linhas.append({
                'protocolo': p, 'arquivo': arquivo,
                'janela': resumo.get('window', 1 if p == 'saw' else '?'),
                'erro_configurado': numero(resumo.get('error')),
                'segmentos': resumo.get('total_segments', len(dados['seq'])),
                'rtt_medio_ms': round(numero(resumo.get('rtt_medio_ms')), 3),
                # É a referência registrada pelo cliente; não é o limite físico da rede.
                'thr_referencia_saw_kbps': round(thr_ref, 3),
                'thr_medido_kbps': round(thr_medido, 3),
                'ganho_vs_saw': '',
                'retransmissoes': resumo.get('retransmissoes_total', sum(dados['retx'])),
                'timeouts_segmentos': sum(1 for x in dados['timeout'] if x),
                'perdas_simuladas': resumo.get('perdas_simuladas_total', sum(dados['perda_simulada'])),
                'duracao_s': round(numero(resumo.get('duracao_s')), 3),
                'bytes_recebidos': resumo.get('bytes_recebidos', 0),
                'md5_ok': resumo.get('checksum_ok', False),
                'md5': resumo.get('checksum_recebido', ''),
            })
            print(f'Gráfico gerado: {graf}')
        except (OSError, ValueError, KeyError, csv.Error, json.JSONDecodeError) as erro:
            print(f'Aviso: não foi possível analisar {caminho_json}: {erro}')
    if not linhas:
        print(f'Nenhum resultado válido encontrado em {pasta}')
        return saida

    # Compara somente resultados do mesmo arquivo e das mesmas condições de teste.
    referencia = {}
    for linha in linhas:
        if linha['protocolo'] == 'saw':
            chave = (linha['arquivo'], linha['erro_configurado'])
            referencia[chave] = numero(linha['thr_medido_kbps'])
    for linha in linhas:
        chave = (linha['arquivo'], linha['erro_configurado'])
        saw = referencia.get(chave, 0)
        if saw > 0:
            linha['ganho_vs_saw'] = round(numero(linha['thr_medido_kbps']) / saw, 3)

    linhas.sort(key=lambda l: (l['arquivo'], l['erro_configurado'],
                               PROTOCOLOS.index(l['protocolo']), str(l['janela'])))
    saida['tabelas'] = list(salvar_tabelas(linhas, pasta))
    if protocolo is None:
        grupos = sorted({(r['arquivo'], r['erro'], str(r['janela'])) for r in registros
                        if r['protocolo'] != 'saw'})
        for arquivo, erro, janela in grupos:
            for campo, titulo, unidade in (
                ('thr_kbps', 'Throughput por segmento', 'kbps'),
                ('rtt_ms', 'RTT por segmento', 'ms'),
                ('retx', 'Retransmissões por segmento', 'quantidade'),
            ):
                caminho = grafico_sobreposto(registros, pasta, arquivo, erro,
                                            janela, campo, titulo, unidade)
                if caminho:
                    saida['comparativos'].append(caminho)
        for campo, ylabel, nome in (
            ('thr_medido_kbps', 'Throughput medido (kbps)', 'comparacao_throughput.png'),
            ('rtt_medio_ms', 'RTT médio (ms)', 'comparacao_rtt.png'),
            ('duracao_s', 'Duração (s)', 'comparacao_duracao.png'),
            ('retransmissoes', 'Retransmissões', 'comparacao_retransmissoes.png'),
            ('timeouts_segmentos', 'Segmentos com timeout', 'comparacao_timeouts.png'),
            ('perdas_simuladas', 'Perdas simuladas', 'comparacao_perdas.png'),
            ('ganho_vs_saw', 'Ganho de throughput em relação ao SAW (×)', 'comparacao_ganho_vs_saw.png'),
        ):
            caminho = grafico_comparativo(linhas, pasta, campo, ylabel, nome)
            if caminho:
                saida['comparativos'].append(caminho)
    # Matriz obrigatória da Entrega 2: uma linha por protocolo/janela.
    for arquivo in sorted({l['arquivo'] for l in linhas}):
        matriz = []
        for prot, jan in [('saw', 1)] + [(p, w) for p in ('gbn', 'sr') for w in (4, 8, 16)]:
            linha = {'protocolo': prot.upper(), 'janela': jan, 'arquivo': arquivo}
            for perda in (0.0, 0.05, 0.10):
                dados = next((l for l in linhas if l['arquivo'] == arquivo
                              and l['protocolo'] == prot
                              and int(l['janela']) == jan
                              and abs(l['erro_configurado'] - perda) < 0.0001), None)
                prefixo = f'p{int(perda*100):02d}'
                linha[f'{prefixo}_throughput_kbps'] = dados['thr_medido_kbps'] if dados else ''
                linha[f'{prefixo}_retransmissoes'] = dados['retransmissoes'] if dados else ''
                linha[f'{prefixo}_duracao_s'] = dados['duracao_s'] if dados else ''
                linha[f'{prefixo}_md5_ok'] = dados['md5_ok'] if dados else ''
            matriz.append(linha)
        caminho_matriz = os.path.join(pasta, f'matriz_entrega2_{arquivo}.csv')
        with open(caminho_matriz, 'w', newline='', encoding='utf-8') as f:
            writer = csv.DictWriter(f, fieldnames=matriz[0].keys())
            writer.writeheader()
            writer.writerows(matriz)
        saida['tabelas'].append(caminho_matriz)
        print(f'Matriz de 21 cenários: {caminho_matriz}')
    print(f'Tabelas: {saida["tabelas"]}')
    print('Gráficos individuais:', sum(len(saida[p]) for p in PROTOCOLOS))
    print('Gráficos comparativos:', len(saida['comparativos']))
    return saida


def analisar_todos_protocolos(pasta='resultados'):
    return Analise_completa(pasta)


def main():
    parser = argparse.ArgumentParser(description='Análise SAW, GBN e SR')
    parser.add_argument('--dir', default='resultados')
    parser.add_argument('--protocol', choices=PROTOCOLOS)
    args = parser.parse_args()
    Analise_completa(args.dir, args.protocol)


if __name__ == '__main__':
    main()