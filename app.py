import datetime as dt

import bcrypt
import pandas as pd
import streamlit as st
from pymongo import MongoClient


# ---------- CONFIG ----------

# Change this if you use MongoDB Atlas
MONGO_URI = "mongodb://localhost:27017"
DB_NAME = "event_stock_db"
APP_TITLE = "Event Stock Control"
APP_SUBTITLE = "Books & Stationery • Exhibitor Stock Dashboard"


# ---------- DB HELPERS ----------

@st.cache_resource
def get_db():
    """Return a MongoDB database handle (cached for the app session)."""
    client = MongoClient(MONGO_URI)
    return client[DB_NAME]


db = get_db()
items_col = db["stock_items"]
mov_col = db["stock_movements"]
users_col = db["users"]


# ---------- USER / AUTH ----------

def create_user(username: str, password: str, is_admin: bool = False):
    existing = users_col.find_one({"username": username})
    if existing:
        raise ValueError("Username already exists.")

    password_hash = bcrypt.hashpw(
        password.encode("utf-8"), bcrypt.gensalt()
    ).decode("utf-8")

    users_col.insert_one(
        {
            "username": username,
            "password_hash": password_hash,
            "is_admin": bool(is_admin),
        }
    )


def authenticate_user(username: str, password: str):
    user = users_col.find_one({"username": username})
    if not user:
        return False, None

    stored_hash = user.get("password_hash")
    if not stored_hash:
        return False, None

    if bcrypt.checkpw(password.encode("utf-8"), stored_hash.encode("utf-8")):
        return True, user
    return False, None


def auth_guard():
    """
    Protects the app behind login.

    - If no users exist: show "create admin" screen.
    - If users exist and no one is logged in: show login.
    """
    if "user" not in st.session_state:
        st.session_state["user"] = None

    # Already logged in
    if st.session_state["user"] is not None:
        return

    # No users yet -> force admin creation
    if users_col.count_documents({}) == 0:
        st.set_page_config(page_title=f"{APP_TITLE} – Setup", page_icon="🔐", layout="wide")
        st.markdown("### 🔐 First-time setup: Create Admin Account")

        with st.form("create_admin_form"):
            username = st.text_input("Admin username")
            pw1 = st.text_input("Password", type="password")
            pw2 = st.text_input("Confirm password", type="password")
            submitted = st.form_submit_button("Create admin")

        if submitted:
            if not username.strip():
                st.error("Username is required.")
            elif pw1 != pw2:
                st.error("Passwords do not match.")
            elif len(pw1) < 4:
                st.warning("Use a password with at least 4 characters.")
            else:
                try:
                    create_user(username.strip(), pw1, is_admin=True)
                    st.success("Admin user created successfully. Please refresh and log in.")
                except ValueError as e:
                    st.error(str(e))
        st.stop()

    # Users exist -> show login form
    st.set_page_config(page_title=f"{APP_TITLE} – Login", page_icon="🔑", layout="wide")
    st.markdown("### 🔑 Login to Event Stock Control")

    with st.form("login_form"):
        username = st.text_input("Username")
        password = st.text_input("Password", type="password")
        submitted = st.form_submit_button("Login")

    if submitted:
        ok, user_doc = authenticate_user(username.strip(), password)
        if ok:
            st.session_state["user"] = {
                "username": user_doc["username"],
                "is_admin": user_doc.get("is_admin", False),
            }
            st.success("Login successful!")
            st.rerun()
        else:
            st.error("Invalid username or password.")

    st.stop()


# ---------- STOCK HELPERS ----------

def get_items_with_current_stock() -> pd.DataFrame:
    """
    Load all items and compute current stock:
    current = open_stock + sum(IN - OUT) movements.
    Returns a pandas DataFrame.
    """
    items = list(items_col.find({}))
    if not items:
        return pd.DataFrame(
            columns=[
                "id",
                "exhibitor_name",
                "item_type",
                "open_stock",
                "movement_delta",
                "current_stock",
            ]
        )

    df_items = pd.DataFrame(items)
    df_items["id"] = df_items["_id"].astype(str)
    df_items["open_stock"] = df_items["open_stock"].fillna(0).astype(int)

    movements = list(mov_col.find({}))
    if movements:
        df_mov = pd.DataFrame(movements)
        df_mov["stock_item_id"] = df_mov["stock_item_id"].astype(str)
        df_mov["quantity"] = df_mov["quantity"].fillna(0).astype(int)

        df_mov["sign"] = df_mov["movement_type"].map({"IN": 1, "OUT": -1}).fillna(0)
        df_mov["delta"] = df_mov["quantity"] * df_mov["sign"]

        df_agg = (
            df_mov.groupby("stock_item_id", as_index=False)["delta"].sum()
            .rename(columns={"delta": "movement_delta"})
        )

        df = df_items.merge(
            df_agg, left_on="id", right_on="stock_item_id", how="left"
        )
        df["movement_delta"] = df["movement_delta"].fillna(0).astype(int)
    else:
        df = df_items.copy()
        df["movement_delta"] = 0

    df["current_stock"] = df["open_stock"] + df["movement_delta"]

    df = df[
        ["id", "exhibitor_name", "item_type", "open_stock", "movement_delta", "current_stock"]
    ].sort_values(by=["exhibitor_name", "item_type"])

    return df


def insert_stock_item(exhibitor_name: str, item_type: str, open_stock: int):
    doc = {
        "exhibitor_name": exhibitor_name,
        "item_type": item_type,
        "open_stock": int(open_stock),
    }
    items_col.insert_one(doc)


def insert_movement(
    stock_item_id: str, movement_type: str, quantity: int, movement_date: dt.date
):
    doc = {
        "stock_item_id": stock_item_id,
        "movement_date": movement_date.isoformat(),
        "movement_type": movement_type,
        "quantity": int(quantity),
    }
    mov_col.insert_one(doc)


def get_movements_for_item(stock_item_id: str) -> pd.DataFrame:
    movements = list(
        mov_col.find({"stock_item_id": stock_item_id}).sort("movement_date", -1)
    )
    if not movements:
        return pd.DataFrame(columns=["movement_date", "movement_type", "quantity"])

    df = pd.DataFrame(movements)[["movement_date", "movement_type", "quantity"]]
    df = df.sort_values(by=["movement_date"], ascending=False)
    return df


# ---------- MAIN APP UI ----------

# Enforce login first
auth_guard()

current_user = st.session_state["user"]
is_admin = current_user.get("is_admin", False)

st.set_page_config(page_title=APP_TITLE, page_icon="📦", layout="wide")

# Light styling
st.markdown(
    """
    <style>
    .top-bar {
        padding: 0.75rem 1rem 0.25rem 1rem;
        border-bottom: 1px solid #eee;
        margin-bottom: 0.5rem;
    }
    .user-pill {
        padding: 0.25rem 0.75rem;
        border-radius: 999px;
        background-color: #f1f3f5;
        font-size: 0.85rem;
        display: inline-block;
        margin-left: 0.5rem;
    }
    .role-pill {
        padding: 0.2rem 0.6rem;
        border-radius: 999px;
        font-size: 0.75rem;
        margin-left: 0.4rem;
        background-color: #e7f5ff;
        color: #1864ab;
    }
    </style>
    """,
    unsafe_allow_html=True,
)

# Top bar
with st.container():
    col_l, col_r = st.columns([4, 2])
    with col_l:
        st.markdown(
            f"""
            <div class="top-bar">
              <h2 style="margin-bottom: 0;">📦 {APP_TITLE}</h2>
              <p style="margin-top: 0.25rem; color: #666; font-size: 0.9rem;">
                {APP_SUBTITLE}
              </p>
            </div>
            """,
            unsafe_allow_html=True,
        )
    with col_r:
        st.markdown(
            f"""
            <div class="top-bar" style="text-align: right;">
              <span class="user-pill">👤 {current_user['username']}</span>
              <span class="role-pill">{'Admin' if is_admin else 'User'}</span>
            </div>
            """,
            unsafe_allow_html=True,
        )

# Sidebar: navigation, logout, admin tools
with st.sidebar:
    st.markdown("### Navigation")
    page = st.radio(
        "",
        ["Dashboard", "Add / Edit Items", "Add Movement"],
    )

    st.markdown("---")
    if st.button("Logout"):
        st.session_state["user"] = None
        st.rerun()

    if is_admin:
        st.markdown("### Admin: Add User")
        with st.form("add_user_form"):
            new_username = st.text_input("New username")
            new_pw1 = st.text_input("Password", type="password")
            new_pw2 = st.text_input("Confirm password", type="password")
            is_admin_new = st.checkbox("Make this user admin?", value=False)
            add_user_sub = st.form_submit_button("Create user")

        if add_user_sub:
            if not new_username.strip():
                st.error("Username is required.")
            elif new_pw1 != new_pw2:
                st.error("Passwords do not match.")
            else:
                try:
                    create_user(new_username.strip(), new_pw1, is_admin=is_admin_new)
                    st.success("User created successfully!")
                except ValueError as e:
                    st.error(str(e))


# ---------- PAGE: DASHBOARD ----------

if page == "Dashboard":
    st.subheader("📊 Stock Overview")

    df = get_items_with_current_stock()

    if df.empty:
        st.info("No items yet. Go to **Add / Edit Items** to create exhibitors and opening stock.")
    else:
        total_exhibitors = df["exhibitor_name"].nunique()
        total_items = len(df)
        total_current_stock = int(df["current_stock"].sum())

        c1, c2, c3 = st.columns(3)
        c1.metric("Exhibitors", total_exhibitors)
        c2.metric("Tracked Items", total_items)
        c3.metric("Total Current Stock", total_current_stock)

        st.markdown("---")

        col_filter, _ = st.columns([2, 1])
        with col_filter:
            exhibitors = ["All"] + sorted(df["exhibitor_name"].unique().tolist())
            selected_exhibitor = st.selectbox("Filter by Exhibitor", exhibitors, index=0)

        df_display = df.copy()
        if selected_exhibitor != "All":
            df_display = df_display[df_display["exhibitor_name"] == selected_exhibitor]

        df_display = df_display.rename(
            columns={
                "exhibitor_name": "Exhibitor",
                "item_type": "Item Type",
                "open_stock": "Open Stock",
                "movement_delta": "Net Movements",
                "current_stock": "Current Stock",
            }
        )

        st.markdown("#### Detailed Stock")
        st.dataframe(
            df_display,
            use_container_width=True,
            hide_index=True,
        )


# ---------- PAGE: ADD / EDIT ITEMS ----------

elif page == "Add / Edit Items":
    st.subheader("➕ Add Exhibitor Opening Stock")

    with st.form("add_item_form"):
        c1, c2, c3 = st.columns([3, 2, 2])
        with c1:
            exhibitor_name = st.text_input("Exhibitor name")
        with c2:
            item_type = st.selectbox("Item type", ["Book", "Stationery"])
        with c3:
            open_stock = st.number_input(
                "Open stock",
                min_value=0,
                step=1,
                value=0,
            )

        submitted = st.form_submit_button("Save item")

    if submitted:
        if not exhibitor_name.strip():
            st.error("Exhibitor name is required.")
        else:
            insert_stock_item(exhibitor_name.strip(), item_type, int(open_stock))
            st.success("Item added successfully!")

    st.markdown("---")
    st.markdown("#### Current Items")

    df = get_items_with_current_stock()
    if df.empty:
        st.info("No items yet.")
    else:
        df_display = df.rename(
            columns={
                "exhibitor_name": "Exhibitor",
                "item_type": "Item Type",
                "open_stock": "Open Stock",
                "movement_delta": "Net Movements",
                "current_stock": "Current Stock",
            }
        )
        st.dataframe(
            df_display,
            use_container_width=True,
            hide_index=True,
        )


# ---------- PAGE: ADD MOVEMENT ----------

elif page == "Add Movement":
    st.subheader("🔁 Add Stock Movement")

    items_df = get_items_with_current_stock()

    if items_df.empty:
        st.info("No items found. First add items in **Add / Edit Items**.")
    else:
        # Unique exhibitors
        exhibitors = sorted(items_df["exhibitor_name"].unique().tolist())

        with st.form("movement_form"):
            # Row 1: Exhibitor + Item Type
            c1, c2 = st.columns([3, 2])
            with c1:
                selected_exhibitor = st.selectbox("Exhibitor", exhibitors)

            # 🔹 NEW: always show Book + Stationery, not filtered
            available_types = ["Book", "Stationery"]
            with c2:
                selected_item_type = st.selectbox("Item type", available_types)

            # Row 2: Movement type + quantity + date
            c3, c4, c5 = st.columns([2, 2, 2])
            with c3:
                movement_type = st.radio("Movement type", ["IN", "OUT"], horizontal=True)
            with c4:
                quantity = st.number_input("Quantity", min_value=1, step=1, value=1)
            with c5:
                movement_date = st.date_input("Movement date", value=dt.date.today())

            submitted_mv = st.form_submit_button("Save movement")

        # Find the item (Exhibitor + ItemType) in DB
        selected_row = items_df[
            (items_df["exhibitor_name"] == selected_exhibitor)
            & (items_df["item_type"] == selected_item_type)
        ]
        stock_item_id = None
        if not selected_row.empty:
            stock_item_id = selected_row.iloc[0]["id"]

        if submitted_mv:
            if stock_item_id is None:
                st.error(
                    f"No stock record found for exhibitor '{selected_exhibitor}' "
                    f"with item type '{selected_item_type}'. "
                    "Go to **Add / Edit Items** and create it first."
                )
            else:
                insert_movement(
                    stock_item_id=stock_item_id,
                    movement_type=movement_type,
                    quantity=int(quantity),
                    movement_date=movement_date,
                )
                st.success("Movement saved!")

        # Show current stock + history for the selected item (if exists)
        if stock_item_id:
            st.markdown("---")
            df_all = get_items_with_current_stock()
            row = df_all[df_all["id"] == stock_item_id].iloc[0]

            c1, c2, c3 = st.columns(3)
            c1.metric("Exhibitor", row["exhibitor_name"])
            c2.metric("Item Type", row["item_type"])
            c3.metric("Current Stock", int(row["current_stock"]))

            st.markdown("#### Recent Movements for this Item")
            hist_df = get_movements_for_item(stock_item_id)
            if hist_df.empty:
                st.write("No movements yet.")
            else:
                st.table(hist_df)
