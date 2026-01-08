import os
import re
import sqlite3
from datetime import datetime, timezone
import time
import pandas as pd
import streamlit as st
from streamlit_autorefresh import st_autorefresh


DB_PATH = "rp_events.db"

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

# =========================
# Confirmar (sem st.stop) + Auto-refresh pros outros verem sem F5
# =========================
with tab_confirmar:
    df = load_event(event_key)

    # ---- Auto-refresh (polling leve) ----
    # IMPORTANTE: garanta que você tem `import time` no topo do arquivo.
# ---- Auto-refresh (não trava o app) ----
    r1, r2 = st.columns([1, 2])
    with r1:
        auto_refresh = st.toggle("Auto-atualizar", value=True, key=f"autorefresh_{event_key}")
    with r2:
        refresh_sec = st.slider("Intervalo (seg)", 2, 30, 5, key=f"refreshsec_{event_key}")

    # dispara rerun automático somente se tiver lista e toggle ligado
    if auto_refresh and not df.empty:
        st_autorefresh(interval=refresh_sec * 1000, key=f"autorefresh_tick_{event_key}")

    if df.empty:
        st.warning("Ainda não existe lista para este evento. Vá na aba ⚙️ para configurar.")
    else:
        total = len(df)
        done = int(df["confirmed"].sum())
        st.progress(done / total if total else 0)
        st.caption(f"{done}/{total} confirmadas")

        cA, cB, cC = st.columns([1, 1, 2])
        with cA:
            show_only_pending = st.toggle("Mostrar só pendentes", value=True, key=f"pending_{event_key}")
        with cB:
            allow_unconfirm = st.toggle("Permitir desconfirmar", value=False, key=f"unconfirm_{event_key}")
        with cC:
            if st.button("🔄 Resetar confirmações deste evento", type="secondary", key=f"reset_{event_key}"):
                reset_event(event_key)
                st.rerun()

        df_view = df[df["confirmed"] == 0].copy() if show_only_pending else df.copy()

        st.divider()

        for _, row in df_view.iterrows():
            org_id = str(row["org_id"])
            org_name = str(row["org_name"])
            checked = True if int(row["confirmed"]) == 1 else False

            left, mid, right = st.columns([2.2, 1, 2.8])

            with left:
                st.write(f"{org_id} | {org_name}")

            with mid:
                disabled = checked and (not allow_unconfirm)
                chk_key = f"chk_{event_key}_{org_id}_{org_name}"
                new_val = st.checkbox("Confirmado", value=checked, disabled=disabled, key=chk_key)

            with right:
                who = row["confirmed_by"] if row["confirmed_by"] else "—"
                at = row["confirmed_at"] if row["confirmed_at"] else "—"
                st.write(f"👤 {who}  |  🕒 {at}")

            if new_val != checked:
                if new_val and not user.strip():
                    st.warning("Digite seu nome antes de confirmar.")
                    st.session_state[chk_key] = checked
                else:
                    set_confirmed(event_key, org_id, org_name, new_val, user)
                st.rerun()

    # ---- Trigger do auto-refresh (no final do tab) ----
    if auto_refresh:
        time.sleep(refresh_sec)
        st.rerun()


# =========================
# Configurar lista (admin)
# =========================
with tab_config:
    st.subheader("Configurar lista do evento")

    admin_password = get_admin_password()

    # trava por senha se existir
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
