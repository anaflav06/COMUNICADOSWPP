import streamlit as st
import pandas as pd
import requests
import base64
import json
import re
import random
import urllib.parse
import unicodedata
from io import BytesIO
from pathlib import Path
from datetime import datetime

st.set_page_config(
    page_title="Comunicados RH",
    page_icon="💬",
    layout="centered",
    initial_sidebar_state="collapsed"
)

# =========================================================
# CONFIGURAÇÃO / BANCO PERSISTENTE
# =========================================================

LOCAL_DB = Path("database_comunicados_rh.json")

def agora():
    return datetime.now().strftime("%d/%m/%Y %H:%M:%S")

def db_vazio():
    return {
        "versao": 1,
        "contatos": {},
        "campanhas": [],
        "ultimo_id_campanha": 0
    }

def github_configurado():
    try:
        return bool(st.secrets.get("GITHUB_TOKEN")) and bool(st.secrets.get("GITHUB_REPO"))
    except Exception:
        return False

def gh_cfg():
    return {
        "token": st.secrets.get("GITHUB_TOKEN", ""),
        "repo": st.secrets.get("GITHUB_REPO", ""),
        "branch": st.secrets.get("GITHUB_DATA_BRANCH", "main"),
        "path": st.secrets.get("GITHUB_RH_DB_PATH", "database_comunicados_rh.json"),
    }

def github_headers():
    cfg = gh_cfg()
    return {
        "Authorization": f"Bearer {cfg['token']}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }

def carregar_db_github():
    cfg = gh_cfg()
    url = f"https://api.github.com/repos/{cfg['repo']}/contents/{cfg['path']}"
    r = requests.get(
        url,
        headers=github_headers(),
        params={"ref": cfg["branch"]},
        timeout=20
    )

    if r.status_code == 404:
        return db_vazio()

    r.raise_for_status()
    payload = r.json()
    conteudo = base64.b64decode(payload["content"]).decode("utf-8")
    dados = json.loads(conteudo)
    return normalizar_db(dados)

def salvar_db_github(dados, mensagem="Atualiza banco Comunicados RH"):
    cfg = gh_cfg()
    url = f"https://api.github.com/repos/{cfg['repo']}/contents/{cfg['path']}"

    sha = None
    r0 = requests.get(
        url,
        headers=github_headers(),
        params={"ref": cfg["branch"]},
        timeout=20
    )
    if r0.status_code == 200:
        sha = r0.json().get("sha")
    elif r0.status_code != 404:
        r0.raise_for_status()

    conteudo = json.dumps(
        normalizar_db(dados),
        ensure_ascii=False,
        indent=2
    ).encode("utf-8")

    body = {
        "message": mensagem,
        "content": base64.b64encode(conteudo).decode("ascii"),
        "branch": cfg["branch"],
    }
    if sha:
        body["sha"] = sha

    r = requests.put(url, headers=github_headers(), json=body, timeout=20)
    r.raise_for_status()

def carregar_db_local():
    if not LOCAL_DB.exists():
        return db_vazio()
    try:
        return normalizar_db(json.loads(LOCAL_DB.read_text(encoding="utf-8")))
    except Exception:
        return db_vazio()

def salvar_db_local(dados):
    LOCAL_DB.write_text(
        json.dumps(normalizar_db(dados), ensure_ascii=False, indent=2),
        encoding="utf-8"
    )

def normalizar_db(dados):
    base = db_vazio()
    if not isinstance(dados, dict):
        return base

    base.update(dados)
    if not isinstance(base.get("contatos"), dict):
        base["contatos"] = {}
    if not isinstance(base.get("campanhas"), list):
        base["campanhas"] = []
    try:
        base["ultimo_id_campanha"] = int(base.get("ultimo_id_campanha", 0))
    except Exception:
        base["ultimo_id_campanha"] = 0

    return base

def carregar_db():
    try:
        if github_configurado():
            return carregar_db_github()
    except Exception as e:
        st.warning(f"Não foi possível ler o banco do GitHub. Usando cópia local nesta execução. Detalhe: {e}")
    return carregar_db_local()

def salvar_db(dados, motivo="Atualiza banco Comunicados RH"):
    salvar_db_local(dados)
    if github_configurado():
        try:
            salvar_db_github(dados, motivo)
        except Exception as e:
            st.error(
                "Os dados foram salvos localmente, mas não consegui atualizar o banco permanente no GitHub. "
                f"Detalhe: {e}"
            )

# =========================================================
# UTILITÁRIOS
# =========================================================

def s(v):
    return "" if pd.isna(v) else str(v).strip()

def normalizar_quebras(texto):
    texto = s(texto)
    texto = texto.replace("\\r\\n", "\n").replace("\\n", "\n")
    texto = texto.replace("\r\n", "\n").replace("\r", "\n")
    return texto

def tel(v):
    d = re.sub(r"\D", "", s(v))

    # Celular de 9 dígitos sem DDD: assume DDD 11.
    if len(d) == 9:
        d = "11" + d

    # Número nacional com DDD: acrescenta Brasil.
    if len(d) in (10, 11):
        d = "55" + d

    return d

def telefone_valido(v):
    return len(tel(v)) in (12, 13)

def formatar_telefone(v):
    d = tel(v)
    if d.startswith("55"):
        d = d[2:]
    if len(d) == 11:
        return f"({d[:2]}) {d[2:7]}-{d[7:]}"
    if len(d) == 10:
        return f"({d[:2]}) {d[2:6]}-{d[6:]}"
    return s(v)

def primeiro_nome(nome):
    partes = s(nome).split()
    return partes[0].title() if partes else "Olá"

def normalizar_cabecalho(v):
    t = s(v).lower()
    t = "".join(
        c for c in unicodedata.normalize("NFKD", t)
        if not unicodedata.combining(c)
    )
    return re.sub(r"[^a-z0-9]+", " ", t).strip()

def achar_coluna(df, opcoes, obrigatoria=True):
    mapa = {normalizar_cabecalho(c): c for c in df.columns}

    for opcao in opcoes:
        alvo = normalizar_cabecalho(opcao)
        if alvo in mapa:
            return mapa[alvo]

    for norm, original in mapa.items():
        for opcao in opcoes:
            alvo = normalizar_cabecalho(opcao)
            if alvo and alvo in norm:
                return original

    if obrigatoria:
        raise ValueError("Não encontrei a coluna: " + " / ".join(opcoes))
    return None

def ler_planilha(upload):
    if upload.name.lower().endswith(".csv"):
        raw = upload.getvalue()
        for enc in ("utf-8-sig", "latin1", "cp1252"):
            for sep in (";", ",", "\t"):
                try:
                    df = pd.read_csv(BytesIO(raw), sep=sep, encoding=enc, dtype=str)
                    if len(df.columns) > 1:
                        return df
                except Exception:
                    pass
        raise ValueError("Não foi possível ler o CSV.")

    return pd.read_excel(upload, dtype=str)

def preparar_base(df, banco):
    nome = achar_coluna(df, ["Nome", "Nome do funcionário", "Nome do colaborador", "Funcionário"])
    empresa = achar_coluna(df, ["Empresa", "Empresa/CNPJ", "CNPJ Empresa", "CNPJ"])
    telefone = achar_coluna(df, ["Telefone", "Telefone celular", "Celular", "WhatsApp"])
    demissao = achar_coluna(df, ["Data de Demissão", "Data Demissão", "Demissão"])

    ativos = df[
        df[demissao].fillna("").astype(str).str.strip().eq("")
    ].copy()

    ativos["nome"] = ativos[nome].map(s)
    ativos["empresa"] = ativos[empresa].map(s)
    ativos["telefone"] = ativos[telefone].map(tel)

    ativos = ativos[ativos["nome"] != ""]
    ativos["chave"] = ativos["nome"].str.upper() + "|" + ativos["empresa"].str.upper()

    # Recupera telefone corrigido/salvo anteriormente.
    telefones_salvos = banco.get("contatos", {})
    novos = []

    for idx, r in ativos.iterrows():
        chave = r["chave"]
        atual = r["telefone"]

        if not telefone_valido(atual):
            salvo = telefones_salvos.get(chave, {}).get("telefone", "")
            if telefone_valido(salvo):
                ativos.at[idx, "telefone"] = salvo

        novos.append({
            "chave": chave,
            "nome": r["nome"],
            "empresa": r["empresa"],
            "telefone": ativos.at[idx, "telefone"],
            "atualizado_em": agora()
        })

    # Atualiza cadastro sem apagar telefone salvo válido.
    for item in novos:
        chave = item["chave"]
        anterior = banco["contatos"].get(chave, {})
        fone = item["telefone"]
        if not telefone_valido(fone) and telefone_valido(anterior.get("telefone", "")):
            fone = anterior["telefone"]

        banco["contatos"][chave] = {
            "nome": item["nome"],
            "empresa": item["empresa"],
            "telefone": fone,
            "atualizado_em": agora()
        }

    salvar_db(banco, "Atualiza colaboradores Comunicados RH")
    return ativos[["chave", "nome", "empresa", "telefone"]]

# =========================================================
# FORMATAÇÃO DA MENSAGEM
# =========================================================

def mensagem_parece_pronta(corpo):
    texto = normalizar_quebras(corpo).strip()
    if not texto:
        return False

    paragrafos = [p.strip() for p in re.split(r"\n\s*\n", texto) if p.strip()]
    marcadores = [
        "comunicado", "importante", "atenção", "lembrete",
        "bom dia", "boa tarde", "boa noite", "pessoal",
        "atenciosamente", "gds logística", "gds logistica"
    ]

    score = 0
    if len(paragrafos) >= 2:
        score += 1
    if any(m in texto.lower() for m in marcadores):
        score += 1
    if len(texto) > 220:
        score += 1

    return score >= 2

def remover_saudacao_coletiva(texto):
    linhas = normalizar_quebras(texto).splitlines()

    saudacoes = [
        r"^pessoal[,! ]*(bom dia|boa tarde|boa noite)?[!. ]*$",
        r"^(bom dia|boa tarde|boa noite)[,! ]*(pessoal|todos|equipe)?[!. ]*$",
        r"^ol[aá][,! ]*(pessoal|todos|equipe)[!. ]*$",
    ]

    for i in range(min(len(linhas), 8)):
        atual = linhas[i].strip()
        if atual and any(re.match(p, atual, re.I) for p in saudacoes):
            linhas.pop(i)
            if i < len(linhas) and not linhas[i].strip():
                linhas.pop(i)
            break

    return "\n".join(linhas).strip()

def aplicar_negritos_estrategicos(texto):
    """
    Apenas formata: não muda valores, datas, regras ou significado.
    """
    t = normalizar_quebras(texto)

    # IMPORTANTE / ATENÇÃO / LEMBRETE
    t = re.sub(
        r"(?<!\*)\b(IMPORTANTE|ATENÇÃO|LEMBRETE)\s*:(?!\*)",
        lambda m: f"*{m.group(1).upper()}:*",
        t,
        flags=re.I
    )

    # Expressões de urgência/ação.
    t = re.sub(
        r"(?<!\*)\bo quanto antes\b(?!\*)",
        "*o quanto antes*",
        t,
        flags=re.I
    )

    # Exemplo muito comum de RH: mudança de benefício/plano.
    t = re.sub(
        r"(?<!\*)\bmudança do plano odontológico\b(?!\*)",
        "*mudança do plano odontológico*",
        t,
        flags=re.I
    )

    # Prestador do exemplo atual.
    t = re.sub(
        r"(?<!\*)\bProdental Brasil\b(?!\*)",
        "*Prodental Brasil*",
        t,
        flags=re.I
    )

    # Evita duplicação acidental de ** no padrão do WhatsApp.
    t = t.replace("**", "*")
    return t

def melhorar_frase_curta(corpo):
    texto = normalizar_quebras(corpo).strip()
    texto = re.sub(r"\s+", " ", texto)

    if texto:
        texto = texto[0].upper() + texto[1:]

    texto = aplicar_negritos_estrategicos(texto)

    if not texto.startswith(("📌", "⚠️", "✅", "ℹ️")):
        texto = "📌 " + texto

    return texto

def montar_mensagem(nome, corpo):
    n = primeiro_nome(nome)
    texto = normalizar_quebras(corpo).strip()

    if mensagem_parece_pronta(texto):
        conteudo = remover_saudacao_coletiva(texto)
        conteudo = aplicar_negritos_estrategicos(conteudo)

        # Mensagem já pronta: só personaliza e dá acabamento leve.
        abertura = f"Olá, {n}! Tudo bem?"

        tem_encerramento = bool(re.search(
            r"(atenciosamente|rh\s*\|\s*gds)",
            conteudo,
            re.I
        ))

        if not tem_encerramento:
            conteudo += "\n\nAtenciosamente,\n\n*RH | GDS Logística*"

        return abertura + "\n\n" + conteudo

    # Frase simples: app pode melhorar apresentação.
    return (
        f"Olá, {n}! Tudo bem? 😊\n\n"
        f"{melhorar_frase_curta(texto)}\n\n"
        f"Atenciosamente,\n\n"
        f"*RH | GDS Logística*"
    )

# =========================================================
# CAMPANHAS
# =========================================================

def nova_campanha(banco, titulo, empresa, mensagem, colaboradores):
    banco["ultimo_id_campanha"] += 1
    cid = banco["ultimo_id_campanha"]

    fila = []
    for _, r in colaboradores.iterrows():
        fila.append({
            "chave": r["chave"],
            "nome": r["nome"],
            "telefone": r["telefone"],
            "status": "PENDENTE",
            "enviado_em": ""
        })

    banco["campanhas"].append({
        "id": cid,
        "titulo": titulo or "Comunicado",
        "empresa": empresa,
        "mensagem": normalizar_quebras(mensagem),
        "criado_em": agora(),
        "status": "EM ANDAMENTO",
        "fila": fila
    })

    salvar_db(banco, f"Cria comunicado {cid}")
    return cid

def buscar_campanha(banco, cid):
    for c in banco["campanhas"]:
        if int(c.get("id", 0)) == int(cid):
            return c
    return None

def atualizar_campanha(banco, campanha, motivo="Atualiza campanha"):
    for i, c in enumerate(banco["campanhas"]):
        if int(c.get("id", 0)) == int(campanha["id"]):
            banco["campanhas"][i] = campanha
            break
    salvar_db(banco, motivo)

# =========================================================
# VISUAL
# =========================================================

st.markdown("""
<style>
#MainMenu, header, footer {visibility:hidden}
.block-container {max-width: 900px; padding-top: 1.3rem; padding-bottom: 3rem}
div.stButton > button {
    width:100%;
    min-height:60px;
    font-size:19px;
    font-weight:700;
    border-radius:14px;
}
div[data-testid="stLinkButton"] a {
    min-height:60px;
    font-size:19px;
    font-weight:700;
    border-radius:14px;
}
.app-title {
    font-size: 42px;
    font-weight: 850;
    margin-bottom: 0;
}
.app-sub {
    font-size: 16px;
    color: #777;
    margin-top: -5px;
    margin-bottom: 25px;
}
.card {
    border: 1px solid #ddd;
    border-radius: 16px;
    padding: 18px;
    margin: 12px 0;
}
</style>
""", unsafe_allow_html=True)

if "pagina" not in st.session_state:
    st.session_state.pagina = "inicio"

banco = carregar_db()

st.markdown('<div class="app-title">💬 COMUNICADOS RH</div>', unsafe_allow_html=True)
st.markdown('<div class="app-sub">Envio individual organizado pelo WhatsApp</div>', unsafe_allow_html=True)

# =========================================================
# TELAS
# =========================================================

def tela_inicio():
    if github_configurado():
        st.success("☁️ Banco permanente conectado.")
    else:
        st.warning(
            "⚠️ Banco permanente do GitHub ainda não está configurado. "
            "No PC os dados ficam salvos localmente; para publicar na nuvem, configure os Secrets."
        )

    if st.button("➕ NOVO COMUNICADO"):
        st.session_state.pagina = "novo"
        st.rerun()

    if st.button("▶️ CONTINUAR ENVIO"):
        st.session_state.pagina = "continuar"
        st.rerun()

    if st.button("🕘 HISTÓRICO"):
        st.session_state.pagina = "historico"
        st.rerun()

def tela_novo():
    if st.button("← VOLTAR"):
        st.session_state.pagina = "inicio"
        st.rerun()

    st.header("1. Carregue a planilha atualizada")

    upload = st.file_uploader(
        "Excel ou CSV",
        type=["xlsx", "xls", "csv"]
    )

    if upload:
        try:
            df = ler_planilha(upload)
            st.session_state.base = preparar_base(df, banco)
            st.success(f"Planilha carregada: {len(st.session_state.base)} colaboradores ativos.")
        except Exception as e:
            st.error(f"Erro ao ler a planilha: {e}")

    if "base" not in st.session_state:
        return

    base = st.session_state.base.copy()

    st.header("2. Escolha a empresa")
    empresas = sorted(x for x in base["empresa"].dropna().unique() if s(x))
    empresa = st.selectbox("Empresa / CNPJ", empresas)

    selecionados = base[base["empresa"] == empresa].copy()
    sem_telefone = selecionados[~selecionados["telefone"].map(telefone_valido)]

    c1, c2, c3 = st.columns(3)
    c1.metric("Ativos", len(selecionados))
    c2.metric("Com telefone", len(selecionados) - len(sem_telefone))
    c3.metric("Sem telefone", len(sem_telefone))

    if len(sem_telefone):
        st.warning(
            "Inclua somente os telefones realmente ausentes/invalidos. "
            "Celulares com 9 dígitos recebem automaticamente o DDD 11."
        )

        alterou = False
        for _, r in sem_telefone.iterrows():
            novo = st.text_input(
                r["nome"],
                value="",
                placeholder="Ex.: 11999999999",
                key="telefone_" + r["chave"]
            )

            if telefone_valido(novo):
                novo_tel = tel(novo)
                st.session_state.base.loc[
                    st.session_state.base["chave"] == r["chave"],
                    "telefone"
                ] = novo_tel

                banco["contatos"][r["chave"]] = {
                    "nome": r["nome"],
                    "empresa": r["empresa"],
                    "telefone": novo_tel,
                    "atualizado_em": agora()
                }
                alterou = True

        if alterou and st.button("💾 SALVAR TELEFONES"):
            salvar_db(banco, "Salva telefones RH")
            st.success("Telefones salvos no banco permanente.")
            st.rerun()

    st.header("3. Escreva o comunicado")

    titulo = st.text_input(
        "Nome do comunicado",
        placeholder="Ex.: NOVO PLANO ODONTOLÓGICO"
    )

    corpo = st.text_area(
        "Texto principal",
        height=220,
        placeholder=(
            "Pode colar uma mensagem já pronta ou escrever apenas a informação principal. "
            "O app identifica o formato automaticamente."
        )
    )

    if corpo and len(selecionados):
        exemplo = selecionados.iloc[0]["nome"]
        st.caption("Prévia da mensagem")
        st.info(montar_mensagem(exemplo, corpo))

    if st.button("INICIAR ENVIOS"):
        base_atual = st.session_state.base
        aptos = base_atual[
            (base_atual["empresa"] == empresa) &
            (base_atual["telefone"].map(telefone_valido))
        ].copy()

        if not corpo.strip():
            st.error("Digite o comunicado.")
            return

        if aptos.empty:
            st.error("Nenhum colaborador com telefone válido nesta empresa.")
            return

        cid = nova_campanha(
            banco,
            titulo.strip() or "Comunicado",
            empresa,
            corpo,
            aptos
        )

        st.session_state.cid = cid
        st.session_state.pagina = "fila"
        st.rerun()

def tela_fila():
    cid = st.session_state.get("cid")
    campanha = buscar_campanha(banco, cid)

    if not campanha:
        st.error("Não encontrei esta campanha.")
        return

    fila = campanha.get("fila", [])
    enviados = [x for x in fila if x["status"] == "ENVIADO"]
    pendentes = [x for x in fila if x["status"] == "PENDENTE"]

    total = len(fila)
    st.progress(len(enviados) / max(total, 1))
    st.subheader(campanha["titulo"])
    st.write(f"**{len(enviados)} de {total} enviados**")

    if not pendentes:
        campanha["status"] = "CONCLUÍDO"
        atualizar_campanha(banco, campanha, "Conclui comunicado")
        st.success("✓ Fila concluída.")

        if st.button("VOLTAR AO INÍCIO"):
            st.session_state.pagina = "inicio"
            st.rerun()
        return

    atual = pendentes[0]
    texto = normalizar_quebras(
        montar_mensagem(atual["nome"], campanha["mensagem"])
    )

    st.markdown(
        f'<div class="card"><h2>{atual["nome"]}</h2>'
        f'<p>{formatar_telefone(atual["telefone"])}</p></div>',
        unsafe_allow_html=True
    )

    st.text_area(
        "Mensagem pronta",
        texto,
        height=280,
        disabled=True
    )

    link = (
        "https://api.whatsapp.com/send?phone="
        + tel(atual["telefone"])
        + "&text="
        + urllib.parse.quote(texto, safe="")
    )

    st.link_button(
        "ABRIR WHATSAPP",
        link,
        use_container_width=True
    )

    st.caption("Confira a conversa e clique em Enviar no próprio WhatsApp.")

    if st.button("✓ ENVIEI — PRÓXIMO"):
        for item in campanha["fila"]:
            if item["chave"] == atual["chave"]:
                item["status"] = "ENVIADO"
                item["enviado_em"] = agora()
                break

        atualizar_campanha(
            banco,
            campanha,
            f"Registra envio comunicado {campanha['id']}"
        )
        st.rerun()

    if st.button("PULAR ESTE COLABORADOR"):
        for item in campanha["fila"]:
            if item["chave"] == atual["chave"]:
                item["status"] = "PULADO"
                break

        atualizar_campanha(
            banco,
            campanha,
            f"Pula colaborador comunicado {campanha['id']}"
        )
        st.rerun()

    if st.button("⏸ PAUSAR E CONTINUAR DEPOIS"):
        atualizar_campanha(
            banco,
            campanha,
            f"Pausa comunicado {campanha['id']}"
        )
        st.session_state.pagina = "inicio"
        st.rerun()

def tela_continuar():
    if st.button("← VOLTAR"):
        st.session_state.pagina = "inicio"
        st.rerun()

    st.header("Continuar envio")

    abertas = [
        c for c in banco["campanhas"]
        if c.get("status") == "EM ANDAMENTO"
    ]
    abertas = sorted(abertas, key=lambda x: x.get("id", 0), reverse=True)

    if not abertas:
        st.info("Não há comunicados em andamento.")
        return

    for c in abertas:
        enviados = sum(1 for x in c.get("fila", []) if x.get("status") == "ENVIADO")
        total = len(c.get("fila", []))

        if st.button(
            f"▶ {c['titulo']} • {c['empresa']} • {enviados}/{total}",
            key=f"continuar_{c['id']}"
        ):
            st.session_state.cid = c["id"]
            st.session_state.pagina = "fila"
            st.rerun()

def tela_historico():
    if st.button("← VOLTAR"):
        st.session_state.pagina = "inicio"
        st.rerun()

    st.header("Histórico")

    campanhas = sorted(
        banco["campanhas"],
        key=lambda x: x.get("id", 0),
        reverse=True
    )

    if not campanhas:
        st.info("Nenhum comunicado registrado.")
        return

    for c in campanhas:
        fila = c.get("fila", [])
        enviados = sum(1 for x in fila if x.get("status") == "ENVIADO")
        pulados = sum(1 for x in fila if x.get("status") == "PULADO")
        total = len(fila)

        with st.expander(
            f"{c['titulo']} • {c['empresa']} • {enviados}/{total}"
        ):
            st.write(f"**Criado em:** {c.get('criado_em','')}")
            st.write(f"**Status:** {c.get('status','')}")
            st.write(f"**Enviados:** {enviados}")
            st.write(f"**Pulados:** {pulados}")
            st.write(f"**Pendentes:** {total - enviados - pulados}")

            st.text_area(
                "Mensagem original",
                normalizar_quebras(c.get("mensagem", "")),
                height=150,
                disabled=True,
                key=f"hist_msg_{c['id']}"
            )

            dados = pd.DataFrame(fila)
            if not dados.empty:
                mostrar = dados[["nome", "telefone", "status", "enviado_em"]].copy()
                mostrar["telefone"] = mostrar["telefone"].map(formatar_telefone)
                mostrar.columns = ["Colaborador", "Telefone", "Status", "Enviado em"]
                st.dataframe(mostrar, use_container_width=True, hide_index=True)

# =========================================================
# ROTEAMENTO
# =========================================================

paginas = {
    "inicio": tela_inicio,
    "novo": tela_novo,
    "fila": tela_fila,
    "continuar": tela_continuar,
    "historico": tela_historico,
}

paginas.get(st.session_state.pagina, tela_inicio)()
