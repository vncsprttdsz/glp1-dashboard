import requests
import json
import os
import time
from datetime import datetime, timedelta
from collections import defaultdict

# API oficial - POST com body JSON
API_URL = "https://api-comexstat.mdic.gov.br/general"

# Período fixo: janeiro/2024 até o mês passado
DATA_INICIO = (2024, 1)
agora = datetime.now()
ultimo_mes = agora.replace(day=1) - timedelta(days=1)
ANO_FIM = ultimo_mes.year
MES_FIM = ultimo_mes.month

# NCMs SEM PONTOS (8 dígitos)
NCM_MARCA = ["30043929"]           # Ozempic / Wegovy / Mounjaro (produto acabado)
NCM_IFA   = ["29332190", "29339990"]  # IFA - semaglutida, tirzepatida

# Mapeamento dos países (como a API retorna em pt-BR)
PAIS_NOVO  = "Dinamarca"
PAIS_LILLY = ["Estados Unidos", "Alemanha"]

def post_com_retry(url, payload, max_tentativas=3, espera_inicial=5):
    """POST com retry em erros de conexão ou 5xx."""
    ultima_resp = None
    for tentativa in range(1, max_tentativas + 1):
        try:
            resp = requests.post(
                url,
                json=payload,
                headers={"Content-Type": "application/json"},
                timeout=60,
            )
            ultima_resp = resp
            if resp.status_code < 500:
                return resp
            print(f"Tentativa {tentativa}: status {resp.status_code}. Body: {resp.text[:300]}")
        except requests.exceptions.RequestException as e:
            print(f"Tentativa {tentativa}: exceção de rede: {e}")
        if tentativa < max_tentativas:
            print(f"Aguardando {espera_inicial}s...")
            time.sleep(espera_inicial)
            espera_inicial *= 2
    if ultima_resp is not None:
        ultima_resp.raise_for_status()
    raise RuntimeError("Falha ao chamar a API após várias tentativas.")

def consultar_ncm(ncm_list, periodo):
    """
    Uma única chamada por consulta (a API aceita várias NCMs no mesmo filters).
    Retorna lista de dicts padronizados: {ano, mes, pais, valor, kg}.
    """
    payload = {
        "flow": "import",
        "monthDetail": True,
        "period": {
            "from": f"{periodo['anoInicial']}-{periodo['mesInicial']:02d}",
            "to":   f"{periodo['anoFinal']}-{periodo['mesFinal']:02d}",
        },
        "filters": [
            {"filter": "ncm", "values": ncm_list}
        ],
        "details": ["country"],
        "metrics": ["metricFOB", "metricKG"],
    }

    resp = post_com_retry(API_URL, payload)
    if resp.status_code != 200:
        print(f"Erro {resp.status_code}: {resp.text[:500]}")
        return []

    body = resp.json()
    # A API /general retorna {"data": {"list": [...], "count": N}, "success": true}
    data = body.get("data", {})
    if isinstance(data, dict):
        lista = data.get("list", [])
    elif isinstance(data, list):
        lista = data  # fallback defensivo
    else:
        lista = []

    if not lista:
        print(f"NCMs {ncm_list}: sem dados. Body de debug: {str(body)[:300]}")
        return []

    registros = []
    for item in lista:
        if not isinstance(item, dict):
            continue
        try:
            ano  = int(item.get("year"))
            mes  = int(item.get("monthNumber"))
            pais = item.get("country", "")
            vl   = float(item.get("metricFOB", 0) or 0)
            kg   = float(item.get("metricKG",  0) or 0)
        except (TypeError, ValueError) as e:
            print(f"Registro ignorado ({e}): {item}")
            continue
        registros.append({"ano": ano, "mes": mes, "pais": pais, "valor": vl, "kg": kg})
    return registros

def agregar_por_mes_e_pais(registros):
    agg = defaultdict(lambda: {"valor": 0.0, "kg": 0.0})
    for r in registros:
        chave = (f"{r['mes']:02d}/{r['ano']}", r["pais"])
        agg[chave]["valor"] += r["valor"]
        agg[chave]["kg"]    += r["kg"]
    return agg

def main():
    periodo = {
        "anoInicial": DATA_INICIO[0],
        "mesInicial": DATA_INICIO[1],
        "anoFinal": ANO_FIM,
        "mesFinal": MES_FIM,
    }

    registros_marca = consultar_ncm(NCM_MARCA, periodo)
    registros_ifa   = consultar_ncm(NCM_IFA,   periodo)

    print(f"Registros marca: {len(registros_marca)} | Registros IFA: {len(registros_ifa)}")

    agg_marca = agregar_por_mes_e_pais(registros_marca)
    agg_ifa   = agregar_por_mes_e_pais(registros_ifa)

    # Série mensal completa
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
        lilly_kg    = sum(agg_marca.get((mes_str, p), {}).get("kg", 0)    for p in PAIS_LILLY)

        marca_total_valor = novo_valor + lilly_valor
        marca_total_kg    = novo_kg + lilly_kg

        ifa_valor = sum(v["valor"] for k, v in agg_ifa.items() if k[0] == mes_str)
        ifa_kg    = sum(v["kg"]    for k, v in agg_ifa.items() if k[0] == mes_str)

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
            "consolidado_valor": marca_total_valor + ifa_valor,
            "consolidado_kg": marca_total_kg + ifa_kg,
        })

    # MoM e YoY em cima do consolidado
    valores = [d["consolidado_valor"] for d in dados_mensais]
    for i, d in enumerate(dados_mensais):
        if i == 0 or valores[i-1] <= 0:
            d["consolidado_mom"] = None
        else:
            d["consolidado_mom"] = round((valores[i] / valores[i-1] - 1) * 100, 1)

        mes_atual, ano_atual = d["mes"].split("/")
        chave_anterior = f"{mes_atual}/{int(ano_atual)-1}"
        idx = next((j for j, it in enumerate(dados_mensais) if it["mes"] == chave_anterior), None)
        if idx is not None and valores[idx] > 0:
            d["consolidado_yoy"] = round((valores[i] / valores[idx] - 1) * 100, 1)
        else:
            d["consolidado_yoy"] = None

    os.makedirs("data", exist_ok=True)
    with open("data/glp1_data.json", "w", encoding="utf-8") as f:
        json.dump(dados_mensais, f, ensure_ascii=False, indent=2)

    print(f"OK: {len(dados_mensais)} meses salvos em data/glp1_data.json")

if __name__ == "__main__":
    main()
