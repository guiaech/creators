import os
import re
import sqlite3
from datetime import datetime, timezone

import pandas as pd
import streamlit as st
from streamlit_autorefresh import st_autorefresh

DB_PATH = "rp_events.db"

# =========================
# Config / Helpers
# =========================

def get_admin_password() -> str:
    """Lê senha via Streamlit Secrets ou ENV."""
    secret_pass = ""
    try:
        secret_pass = st.secrets.get("ADMIN_PASSWORD", "")
    except Exception:
        pass
    return os.getenv("ADMIN_PASSWORD", "") or secret_pass or ""

def utc_now_str() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

def get_conn():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL;")
    return conn

def init_db():
    """
    Tabela com STATUS:
      PENDING = ainda não falou
      YES     = vai
      NO      = não vai
    """
    conn = get_conn()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS confirmations (
            event_key   TEXT NOT NULL,
            org_id      TEXT NOT NULL,
            org_name    TEXT NOT NULL,
            status      TEXT NOT NULL DEFAULT 'PENDING',  -- PENDING/YES/NO
            updated_by  TEXT,
            updated_at  TEXT,
            PRIMARY KEY (event_key, org_id, org_name)
        );
    """)
    conn.commit()
    conn.close()

def parse_orgs(text: str):
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

        org_id = org_id_raw.zfill(2) if len(org_id_raw) == 1 else org_id_raw
        orgs.append((org_id, org_name))

    # remove duplicados mantendo ordem
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
    """Substitui a lista do evento e zera tudo como PENDING."""
    conn = get_conn()
    conn.execute("DELETE FROM confirmations WHERE event_key = ?", (event_key,))
    conn.executemany("""
        INSERT INTO confirmations (event_key, org_id, org_name, status)
        VALUES (?, ?, ?, 'PENDING')
    """, [(event_key, oid, oname) for oid, oname in orgs])
    conn.commit()
    conn.close()

def load_event(event_key: str) -> pd.DataFrame:
    conn = get_conn()
    df = pd.read_sql_query("""
        SELECT org_id, org_name, status, updated_by, updated_at
        FROM confirmations
        WHERE event_key = ?
        ORDER BY CAST(org_id AS INTEGER), org_name
    """, conn, params=(event_key,))
    conn.close()
    return df

def set_status(event_key: str, org_id: str, org_name: str, status: str, who: str):
    """Atualiza status (PENDING/YES/NO) + auditoria."""
    conn = get_conn()
    conn.execute("""
        UPDATE confirmations
        SET status = ?,
            updated_by = ?,
            updated_at = ?
        WHERE event_key = ? AND org_id = ? AND org_name = ?
    """, (status, who.strip() or "—", utc_now_str(), event_key, org_id, org_name))
    conn.commit()
    conn.close()

def reset_event(event_key: str):
    conn = get_conn()
    conn.execute("""
        UPDATE confirmations
        SET status = 'PENDING', updated_by = NULL, updated_at = NULL
        WHERE event_key = ?
    """, (event_key,))
    conn.commit()
    conn.close()

# =========================
# UI
# =========================
st.set_page_config(page_title="Confirmar ORGs - RP", layout="wide")
init_db()

st.title("✅ Confirmar ORGs - SANTA CREATORS")

col1, col2, col3 = st.columns([1.2, 1.2, 2.2])
with col1:
    event_day = st.selectbox("Dia do evento", ["Quinta", "Sexta", "Sábado"], key="event_day")
with col2:
    event_date = st.date_input("Data", key="event_date")
with col3:
    user = st.text_input("Seu nome (@discord)", placeholder="Ex: @guilherme", key="user_name")

event_key = f"{event_day}-{event_date.isoformat()}"

tab_confirmar, tab_config = st.tabs(["✅ Confirmar", "⚙️ Configurar lista (admin)"])

STATUS_LABELS = {
    "PENDING": "⏳ Pendente",
    "YES": "✅ Vai",
    "NO": "❌ Não vai",
}
LABEL_TO_STATUS = {v: k for k, v in STATUS_LABELS.items()}
STATUS_OPTIONS = list(STATUS_LABELS.values())

# =========================
# Confirmar (sem st.stop) + Auto-refresh sem travar
# =========================
with tab_confirmar:
    df = load_event(event_key)

    # Auto-refresh (para outros verem sem F5)
    r1, r2 = st.columns([1, 2])
    with r1:
        auto_refresh = st.toggle("Auto-atualizar", value=True, key=f"autorefresh_{event_key}")
    with r2:
        refresh_sec = st.slider("Intervalo (seg)", 2, 30, 5, key=f"refreshsec_{event_key}")

    if auto_refresh and not df.empty:
        st_autorefresh(interval=refresh_sec * 1000, key=f"autorefresh_tick_{event_key}")

    if df.empty:
        st.warning("Ainda não existe lista para este evento. Vá na aba ⚙️ para configurar.")
    else:
        total = len(df)
        yes = int((df["status"] == "YES").sum())
        no = int((df["status"] == "NO").sum())
        pend = int((df["status"] == "PENDING").sum())

        st.caption(f"✅ Vai: {yes}  |  ❌ Não vai: {no}  |  ⏳ Pendentes: {pend}  |  Total: {total}")
        st.progress((yes + no) / total if total else 0)

        cA, cB, cC = st.columns([1.3, 1.3, 2])

        with cA:
            filtro = st.selectbox(
                "Filtro",
                ["Todos", "Pendentes", "Vai", "Não vai"],
                key=f"filter_{event_key}",
            )

        with cB:
            # trava desconfirmar? aqui é "travar voltar p/ pendente"
            lock_after_set = st.toggle(
                "Travar após definir (opcional)",
                value=False,
                key=f"lock_{event_key}",
                help="Se ligado, depois que marcar 'Vai' ou 'Não vai', não deixa voltar sem destravar."
            )

        with cC:
            if st.button("🔄 Resetar status deste evento", type="secondary", key=f"reset_{event_key}"):
                reset_event(event_key)
                st.rerun()

        if filtro == "Pendentes":
            df_view = df[df["status"] == "PENDING"].copy()
        elif filtro == "Vai":
            df_view = df[df["status"] == "YES"].copy()
        elif filtro == "Não vai":
            df_view = df[df["status"] == "NO"].copy()
        else:
            df_view = df.copy()

        st.divider()

        for _, row in df_view.iterrows():
            org_id = str(row["org_id"])
            org_name = str(row["org_name"])
            current_status = row["status"] if row["status"] else "PENDING"

            left, mid, right = st.columns([2.2, 1.4, 2.4])

            with left:
                st.write(f"{org_id} | {org_name}")

            with mid:
                sel_key = f"status_{event_key}_{org_id}_{org_name}"
                current_label = STATUS_LABELS.get(current_status, STATUS_LABELS["PENDING"])

                # se travar após set, desabilita quando status != PENDING
                disabled = bool(lock_after_set and current_status in ("YES", "NO"))

                new_label = st.selectbox(
                    "Status",
                    STATUS_OPTIONS,
                    index=STATUS_OPTIONS.index(current_label),
                    key=sel_key,
                    label_visibility="collapsed",
                    disabled=disabled,
                )

            with right:
                who = row["updated_by"] if row["updated_by"] else "—"
                at = row["updated_at"] if row["updated_at"] else "—"
                st.write(f"👤 {who}  |  🕒 {at}")

            new_status = LABEL_TO_STATUS[new_label]
            if new_status != current_status:
                if not user.strip():
                    st.warning("Digite seu nome antes de atualizar o status.")
                    st.session_state[sel_key] = current_label
                else:
                    set_status(event_key, org_id, org_name, new_status, user)
                st.rerun()

# =========================
# Configurar lista (admin)
# =========================
with tab_config:
    st.subheader("Configurar lista do evento")

    admin_password = get_admin_password()

    if admin_password:
        admin_pass = st.text_input("Senha admin", type="password", key="admin_pass")

        if not admin_pass:
            st.info("Digite a senha para liberar a configuração.")
        elif admin_pass != admin_password:
            st.error("Senha incorreta.")
        else:
            st.success("Acesso liberado.")
            st.write("Cole a lista (1 por linha). Ex: `08 | Caribe` ou `08 Caribe`")

            pasted = st.text_area(
                "Lista de organizações",
                height=260,
                placeholder="08 | Caribe\n53 | Rússia\n57 | Japão\n...",
                key=f"orgs_text_{event_key}",
            )

            colA, colB = st.columns([1, 1.6])
            with colA:
                if st.button("🔎 Validar", key=f"valid_{event_key}"):
                    parsed = parse_orgs(pasted)
                    if not parsed:
                        st.error("Não encontrei linhas válidas.")
                    else:
                        st.success(f"OK! {len(parsed)} organizações detectadas.")
                        st.dataframe(pd.DataFrame(parsed, columns=["org_id", "org_name"]), use_container_width=True)

            with colB:
                if st.button("💾 Salvar lista do evento (substituir)", type="primary", key=f"save_{event_key}"):
                    parsed = parse_orgs(pasted)
                    if not parsed:
                        st.error("Lista vazia ou inválida.")
                    else:
                        replace_event_list(event_key, parsed)
                        st.success("Lista salva! Vá na aba ✅ para confirmar.")
                        st.rerun()

    else:
        st.warning("⚠️ ADMIN_PASSWORD não definido. Qualquer pessoa pode editar a lista.")
        st.write("Cole a lista (1 por linha). Ex: `08 | Caribe` ou `08 Caribe`")

        pasted = st.text_area(
            "Lista de organizações",
            height=260,
            placeholder="08 | Caribe\n53 | Rússia\n57 | Japão\n...",
            key=f"orgs_text_open_{event_key}",
        )

        colA, colB = st.columns([1, 1.6])
        with colA:
            if st.button("🔎 Validar", key=f"valid_open_{event_key}"):
                parsed = parse_orgs(pasted)
                if not parsed:
                    st.error("Não encontrei linhas válidas.")
                else:
                    st.success(f"OK! {len(parsed)} organizações detectadas.")
                    st.dataframe(pd.DataFrame(parsed, columns=["org_id", "org_name"]), use_container_width=True)

        with colB:
            if st.button("💾 Salvar lista do evento (substituir)", type="primary", key=f"save_open_{event_key}"):
                parsed = parse_orgs(pasted)
                if not parsed:
                    st.error("Lista vazia ou inválida.")
                else:
                    replace_event_list(event_key, parsed)
                    st.success("Lista salva! Vá na aba ✅ para confirmar.")
                    st.rerun()
