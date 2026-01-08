import streamlit as st
import sqlite3
from datetime import datetime, timezone
import pandas as pd
import re
import os

# ----------------------------
# Config
# ----------------------------
DB_PATH = "rp_events.db"

# Defina a senha por ENV var (recomendado) ou secrets no Streamlit Cloud.
# Ex: ADMIN_PASSWORD="minhasenha"
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "")

def utc_now_str():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

def get_conn():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL;")  # melhor pra múltiplos acessos
    return conn

def init_db():
    conn = get_conn()
    conn.execute("""
    CREATE TABLE IF NOT EXISTS confirmations (
        event_key TEXT NOT NULL,
        org_id TEXT NOT NULL,
        org_name TEXT NOT NULL,
        confirmed INTEGER NOT NULL DEFAULT 0,
        confirmed_by TEXT,
        confirmed_at TEXT,
        PRIMARY KEY (event_key, org_id, org_name)
    );
    """)
    conn.commit()
    conn.close()

def parse_orgs(text: str):
    """
    Aceita linhas no formato:
    08 | Caribe
    08 Caribe
    8 | Caribe  (vira 08)
    """
    orgs = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue

        m = re.match(r"^\s*(\d+)\s*(?:\|\s*)?(.*)\s*$", line)
        if not m:
            continue

        org_id_raw = m.group(1).strip()
        org_name = m.group(2).strip()

        if not org_name:
            continue

        # padroniza ID 1 dígito -> 0X, mas mantém "00" como está se vier assim
        if len(org_id_raw) == 1:
            org_id = org_id_raw.zfill(2)
        else:
            org_id = org_id_raw

        orgs.append((org_id, org_name))

    # remove duplicados mantendo ordem (por id + nome)
    seen = set()
    uniq = []
    for oid, on in orgs:
        key = (oid, on.lower())
        if key in seen:
            continue
        seen.add(key)
        uniq.append((oid, on))

    return uniq

def replace_event_list(event_key: str, orgs: list[tuple[str, str]]):
    conn = get_conn()
    conn.execute("DELETE FROM confirmations WHERE event_key = ?", (event_key,))
    conn.executemany("""
        INSERT INTO confirmations (event_key, org_id, org_name, confirmed)
        VALUES (?, ?, ?, 0)
    """, [(event_key, oid, oname) for oid, oname in orgs])
    conn.commit()
    conn.close()

def load_event(event_key: str) -> pd.DataFrame:
    conn = get_conn()
    df = pd.read_sql_query("""
        SELECT org_id, org_name, confirmed, confirmed_by, confirmed_at
        FROM confirmations
        WHERE event_key = ?
        ORDER BY CAST(org_id AS INTEGER), org_name
    """, conn, params=(event_key,))
    conn.close()
    if not df.empty:
        df["confirmed"] = df["confirmed"].astype(int)
    return df

def set_confirmed(event_key: str, org_id: str, org_name: str, confirmed: bool, who: str):
    conn = get_conn()
    if confirmed:
        conn.execute("""
            UPDATE confirmations
            SET confirmed = 1,
                confirmed_by = ?,
                confirmed_at = ?
            WHERE event_key = ? AND org_id = ? AND org_name = ?
        """, (who.strip() or "—", utc_now_str(), event_key, org_id, org_name))
    else:
        conn.execute("""
            UPDATE confirmations
            SET confirmed = 0,
                confirmed_by = NULL,
                confirmed_at = NULL
            WHERE event_key = ? AND org_id = ? AND org_name = ?
        """, (event_key, org_id, org_name))
    conn.commit()
    conn.close()

def reset_event(event_key: str):
    conn = get_conn()
    conn.execute("""
        UPDATE confirmations
        SET confirmed = 0, confirmed_by = NULL, confirmed_at = NULL
        WHERE event_key = ?
    """, (event_key,))
    conn.commit()
    conn.close()

# ----------------------------
# UI
# ----------------------------
st.set_page_config(page_title="Confirmar ORGs - SANTA CREATORS", layout="wide")
init_db()

st.title("✅ Confirmar ORGs - SANTA CREATORS")

col1, col2, col3 = st.columns([1.2, 1.2, 2.2])
with col1:
    event_day = st.selectbox("Dia do evento", ["Quinta", "Sexta", "Sábado"])
with col2:
    event_date = st.date_input("Data")
with col3:
    user = st.text_input("Seu nome (@discord)", placeholder="Ex: @guilherme")

event_key = f"{event_day}-{event_date.isoformat()}"

tab_confirmar, tab_config = st.tabs(["✅ Confirmar", "⚙️ Configurar lista (admin)"])

# ----------------------------
# Aba Confirmar (usuários)
# ----------------------------
with tab_confirmar:
    df = load_event(event_key)

    if df.empty:
        st.warning("Ainda não existe lista para este evento. Peça para o admin configurar na aba ⚙️.")
        st.stop()

    total = len(df)
    done = int(df["confirmed"].sum())
    st.progress(done / total if total else 0)
    st.caption(f"{done}/{total} confirmadas")

    cA, cB, cC = st.columns([1, 1, 2])
    with cA:
        show_only_pending = st.toggle("Mostrar só pendentes", value=True)
    with cB:
        allow_unconfirm = st.toggle("Permitir desconfirmar", value=False)
    with cC:
        if st.button("🔄 Resetar confirmações deste evento", type="secondary"):
            reset_event(event_key)
            st.rerun()

    if show_only_pending:
        df_view = df[df["confirmed"] == 0].copy()
    else:
        df_view = df.copy()

    st.divider()

    for _, row in df_view.iterrows():
        org_label = f"{row['org_id']} | {row['org_name']}"
        left, mid, right = st.columns([2.2, 1, 2.8])

        with left:
            st.write(org_label)

        with mid:
            disabled = (row["confirmed"] == 1) and (not allow_unconfirm)
            checked = True if row["confirmed"] == 1 else False
            key = f"chk-{event_key}-{row['org_id']}-{row['org_name']}"
            new_val = st.checkbox("Confirmado", value=checked, key=key, disabled=disabled)

        with right:
            who = row["confirmed_by"] if row["confirmed_by"] else "—"
            at = row["confirmed_at"] if row["confirmed_at"] else "—"
            st.write(f"👤 {who}  |  🕒 {at}")

        if new_val != checked:
            if new_val and not user.strip():
                st.warning("Digite seu nome antes de confirmar.")
                st.session_state[key] = checked
            else:
                set_confirmed(event_key, row["org_id"], row["org_name"], new_val, user)
            st.rerun()

# ----------------------------
# Aba Configurar (admin)
# ----------------------------
with tab_config:
    st.subheader("Configurar lista do evento")

    # se não tiver senha definida, deixa aberto (mas eu recomendo definir)
    if ADMIN_PASSWORD:
        admin_pass = st.text_input("Senha admin", type="password", placeholder="Senha do admin")
        if admin_pass != ADMIN_PASSWORD:
            st.info("Digite a senha admin para editar a lista.")
            st.stop()
    else:
        st.warning("⚠️ ADMIN_PASSWORD não definido. Qualquer pessoa pode editar a lista (defina no cloud!).")

    st.write("Cole a lista (1 por linha). Exemplos aceitos: `08 | Caribe` ou `08 Caribe`")

    pasted = st.text_area(
        "Lista de organizações",
        height=260,
        placeholder="08 | Caribe\n53 | Rússia\n57 | Japão\n..."
    )

    colA, colB = st.columns([1, 1.6])

    with colA:
        if st.button("🔎 Validar"):
            parsed = parse_orgs(pasted)
            if not parsed:
                st.error("Não encontrei linhas válidas. Use o formato: 08 | Caribe")
            else:
                st.success(f"OK! {len(parsed)} organizações detectadas.")
                st.dataframe(pd.DataFrame(parsed, columns=["org_id", "org_name"]), use_container_width=True)

    with colB:
        if st.button("💾 Salvar lista do evento (substituir)", type="primary"):
            parsed = parse_orgs(pasted)
            if not parsed:
                st.error("Lista vazia ou inválida.")
            else:
                replace_event_list(event_key, parsed)
                st.success("Lista salva! Agora a equipe pode confirmar na aba ✅.")
                st.rerun()

    st.divider()
    st.caption("Dica: você pode montar a lista do dia copiando do Discord e colando aqui.")
