import requests
import json
import os
import time
from datetime import datetime, timedelta
from collections import defaultdict

# API oficial (com hífen e rota /general com filtro GET)
API_URL = "https://api-comexstat.mdic.gov.br/general"

# Período fixo: janeiro/2024 até o mês passado
DATA_INICIO = (2024, 1)
agora = datetime.now()
ultimo_mes = agora.replace(day=1) - timedelta(days=1)
ANO_FIM = ultimo_mes.year
MES_FIM = ultimo_mes.month

# Blocos de monitoramento
NCM_MARCA = ["3004.39.29"]
NCM_IFA = ["2933.21.90", "2933.90.90"]

# Mapeamento dos países (nomes como a API retorna: "Dinamarca", "Estados Unidos", "Alemanha")
PAIS_NOVO = "Dinamarca"
PAIS_LILLY = ["Estados Unidos", "Alemanha"]

def get_com_retry(url, max_tentativas=3, espera_inicial=5):
    """Tenta um GET, repetindo em caso de erro de conexão ou 5xx."""
    for tentativa in range(1, max_tentativas + 1):
        try:
            resp = requests.get(url, timeout=30)
            if resp.status_code < 500:
                return resp
            else:
                print(f"Tentativa {tentativa}: status {resp.status_code}. Resposta: {resp.text[:200]}")
        except requests.exceptions.RequestException as e:
            print(f"Tentativa {tentativa}: exceção de rede: {e}")
        if tentativa < max_tentativas:
            print(f"Aguardando {espera_inicial} segundos...")
            time.sleep(espera_inicial)
    resp = requests.get(url, timeout=30)
    resp.raise_for_status()
    return resp

def consultar_ncm(ncm_list, periodo):
    """Retorna todos os registros de importação para uma lista de NCMs, já como lista de dicionários."""
    registros = []
    for ncm in ncm_list:
        payload = {
            "tipoFiltro": "NCM",
            "filtro": ncm,
            "fluxo": "importacao",
            "periodo": periodo,
            "detalhamento": ["pais", "mes"]
        }
        filter_str = json.dumps(payload)
        url = f"{API_URL}?filter={requests.utils.quote(filter_str)}"
        resp = get_com_retry(url)
        if resp.status_code == 200:
            resposta = resp.json()
            dados = resposta.get("data", [])
            if not dados:
                print(f"NCM {ncm}: sem dados para o período.")
                continue

            # --- DEBUG: mostra o tipo e o primeiro elemento para entendermos a estrutura ---
            print(f"DEBUG NCM {ncm}: tipo de 'data' = {type(dados)}")
            if isinstance(dados, list) and len(dados) > 0:
                print(f"DEBUG primeiro elemento: tipo={type(dados[0])}, valor={dados[0]}")
            # ----------------------------------------------------------------------------

            # Converte cada item para dicionário se necessário
            for item in dados:
                if isinstance(item, str):
                    # É uma string JSON -> parse
                    try:
                        obj = json.loads(item)
                    except json.JSONDecodeError:
                        print(f"Item ignorado (string não é JSON válido): {item[:100]}")
                        continue
                    registros.append(obj)
                elif isinstance(item, dict):
                    registros.append(item)
                else:
                    print(f"Tipo inesperado no registro: {type(item)} -> {item}")
        else:
            print(f"Erro na consulta NCM {ncm}: {resp.status_code} {resp.text}")
    return registros

def agregar_por_mes_e_pais(registros):
    """Agrupa vlFob e kgLiquido por (mês, país)."""
    agg = defaultdict(lambda: {"valor": 0.0, "kg": 0.0})
    for r in registros:
        # Agora r é garantidamente um dicionário (ou foi ignorado)
        if not isinstance(r, dict):
            print(f"Registro ignorado (não é dict): {r}")
            continue
        try:
            ano = str(r.get("coAno", ""))
            mes = str(r.get("coMes", "")).zfill(2)
            pais = r.get("noPais", "")
            vl = float(r.get("vlFob", 0))
            kg = float(r.get("kgLiquido", 0))
        except (ValueError, TypeError) as e:
            print(f"Erro ao processar registro: {r} -> {e}")
            continue

        chave_mes = f"{mes}/{ano}"
        chave = (chave_mes, pais)
        agg[chave]["valor"] += vl
        agg[chave]["kg"] += kg
    return agg

def main():
    periodo = {
        "anoInicial": DATA_INICIO[0],
        "mesInicial": DATA_INICIO[1],
        "anoFinal": ANO_FIM,
        "mesFinal": MES_FIM
    }

    # 1. Coleta
    registros_marca = consultar_ncm(NCM_MARCA, periodo)
    registros_ifa = consultar_ncm(NCM_IFA, periodo)

    # 2. Agregação
    agg_marca = agregar_por_mes_e_pais(registros_marca)
    agg_ifa = agregar_por_mes_e_pais(registros_ifa)

    # 3. Construção das séries mensais
    meses = []
    ano, mes = DATA_INICIO
    while (ano, mes) <= (ANO_FIM, MES_FIM):
        meses.append(f"{mes:02d}/{ano}")
        mes += 1
        if mes > 12:
            mes = 1
            ano += 1

    dados_mensais = []
    for mes_str in meses:
        novo_valor = agg_marca.get((mes_str, PAIS_NOVO), {}).get("valor", 0)
        novo_kg    = agg_marca.get((mes_str, PAIS_NOVO), {}).get("kg", 0)

        lilly_valor = sum(agg_marca.get((mes_str, p), {}).get("valor", 0) for p in PAIS_LILLY)
        lilly_kg    = sum(agg_marca.get((mes_str, p), {}).get("kg", 0) for p in PAIS_LILLY)

        marca_total_valor = novo_valor + lilly_valor
        marca_total_kg = novo_kg + lilly_kg

        ifa_valor = sum(vals["valor"] for chave, vals in agg_ifa.items() if chave[0] == mes_str)
        ifa_kg    = sum(vals["kg"]    for chave, vals in agg_ifa.items() if chave[0] == mes_str)

        consolidado_valor = marca_total_valor + ifa_valor
        consolidado_kg = marca_total_kg + ifa_kg

        dados_mensais.append({
            "mes": mes_str,
            "novo_valor": novo_valor,
            "novo_kg": novo_kg,
            "lilly_valor": lilly_valor,
            "lilly_kg": lilly_kg,
            "marca_total_valor": marca_total_valor,
            "marca_total_kg": marca_total_kg,
            "ifa_valor": ifa_valor,
            "ifa_kg": ifa_kg,
            "consolidado_valor": consolidado_valor,
            "consolidado_kg": consolidado_kg
        })

    # 4. Crescimentos
    valores_consolidado = [d["consolidado_valor"] for d in dados_mensais]
    for i, d in enumerate(dados_mensais):
        if i == 0:
            d["consolidado_mom"] = None
        else:
            anterior = valores_consolidado[i-1]
            d["consolidado_mom"] = round((valores_consolidado[i] / anterior - 1) * 100, 1) if anterior > 0 else None

        partes = d["mes"].split("/")
        mes_atual = int(partes[0])
        ano_atual = int(partes[1])
        chave_anterior = f"{mes_atual:02d}/{ano_atual-1}"
        idx = next((j for j, item in enumerate(dados_mensais) if item["mes"] == chave_anterior), None)
        if idx is not None:
            val_anterior = dados_mensais[idx]["consolidado_valor"]
            d["consolidado_yoy"] = round((valores_consolidado[i] / val_anterior - 1) * 100, 1) if val_anterior > 0 else None
        else:
            d["consolidado_yoy"] = None

    # 5. Salvar
    os.makedirs("data", exist_ok=True)
    with open("data/glp1_data.json", "w", encoding="utf-8") as f:
        json.dump(dados_mensais, f, ensure_ascii=False, indent=2)

    print("Dados atualizados com sucesso.")

if __name__ == "__main__":
    main()
