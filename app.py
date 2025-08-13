# app.py
# Streamlit app to verify iClass transactions using Moniepoint statement credits.
#
# HOW TO RUN
#   pip install streamlit pandas numpy xlsxwriter openpyxl
#   streamlit run app.py
#
# WHAT IT DOES
# - Upload two CSVs (iClass + Moniepoint)
# - Map columns (pre-filled to your headers)
# - Pick bank amount source (Net Settlement, Gross Amount Paid, or Net = Gross - Charge)
# - Params: date window (±days), amount tolerance (₦), ref extraction, strict ref match
# - Matching: amount + date window (+ ref exact / ref-in-narration as signals), greedy one-to-one
# - Dashboard: row counts, money totals, daily totals, match pairs
# - Downloads: Excel (3 sheets) + CSVs

import io
import re
from datetime import timedelta
from difflib import SequenceMatcher

import numpy as np
import pandas as pd
import streamlit as st

# ======================= Helpers =======================
def to_date(x):
    if pd.isna(x):
        return pd.NaT
    return pd.to_datetime(x, errors="coerce").normalize()

def to_amount(x):
    if pd.isna(x):
        return np.nan
    s = str(x)
    s = s.replace(",", "").replace("NGN", "").replace("₦", "").strip()
    try:
        return float(s)
    except:  # noqa: E722
        return np.nan

def norm_ref(x):
    if pd.isna(x):
        return None
    s = re.sub(r"[^A-Za-z0-9]", "", str(x)).upper()
    return s if len(s) >= 6 else None

def extract_ref_from_text(text):
    if pd.isna(text):
        return None
    s = re.sub(r"[^A-Za-z0-9]", "", str(text)).upper()
    tokens = re.findall(r"[A-Z0-9]{6,}", s)
    if not tokens:
        return None
    tokens.sort(key=lambda t: (-len(t), t))
    return tokens[0]

def text_sim(a, b):
    a = "" if pd.isna(a) else str(a)
    b = "" if pd.isna(b) else str(b)
    return SequenceMatcher(None, a.lower(), b.lower()).ratio()

def df_columns_safe(df):
    return list(df.columns) if df is not None else []

def default_index(options, default_value):
    return options.index(default_value) if options and default_value in options else (0 if options else 0)

def date_variants(d, window):
    if pd.isna(d):
        return [pd.NaT]
    base = pd.to_datetime(d)
    return [base + timedelta(days=k) for k in range(-window, window + 1)]

def money(n):
    try:
        return f"₦{float(n):,.2f}"
    except Exception:
        return "₦0.00"

# ======================= UI =======================
st.set_page_config(page_title="iClass ↔ Moniepoint Reconciliation", layout="wide")
st.title("iClass ↔ Moniepoint Reconciliation")
st.caption("Verify iClass transactions using Moniepoint statement credits. One-to-one greedy assignment; explainable scores.")

with st.sidebar:
    st.header("1) Upload CSVs")
    iclass_file = st.file_uploader("iClass.csv", type=["csv"])
    bank_file   = st.file_uploader("Moniepoint bank statement.csv", type=["csv"])

    st.header("2) Parameters")
    date_window = st.slider("Date window (± days)", min_value=0, max_value=3, value=1, step=1)
    amount_tol  = st.number_input("Amount tolerance (₦)", min_value=0.0, max_value=10000.0, value=0.0, step=1.0)

    enrich_ref_from_bank_narr = st.checkbox("Extract missing bank refs from Narration", value=True)
    require_exact_ref = st.checkbox("Require exact reference to match (strict)", value=False)

    st.header("3) Assignment")
    st.write("Greedy one-to-one matching (each bank credit verifies at most one iClass row).")
    run_btn = st.button("Run Reconciliation", type="primary", use_container_width=True)

# Load files if present
ic_df = pd.read_csv(iclass_file) if iclass_file is not None else None
bk_df = pd.read_csv(bank_file)   if bank_file   is not None else None

# Defaults aligned to your dataset
default_ic_date   = "DATE"
default_ic_amount = "AMOUNT PAID"
default_ic_ref_a  = "RECEIPT REF."
default_ic_ref_b  = "INVOICE REF."
default_ic_descs  = ["CLIENT NAME", "PAYMENT MODE"]

default_b_date    = "Date"
default_b_credit  = "Settlement Credit (NGN)"  # net
default_b_ref     = "Transaction Reference"
default_b_desc    = "Narration"
default_b_amtpaid = "Amount Paid"              # gross
default_b_charge  = "Charge (NGN)"

st.header("Column Mapping")
if ic_df is None or bk_df is None:
    st.info("Upload your two CSVs in the sidebar to configure column mapping.")
    ic_columns, b_columns = [], []
else:
    ic_columns = df_columns_safe(ic_df)
    b_columns  = df_columns_safe(bk_df)

col1, col2 = st.columns(2)
with col1:
    st.subheader("iClass")
    ic_date   = st.selectbox("Date", options=ic_columns, index=default_index(ic_columns, default_ic_date), key="ic_date")
    ic_amount = st.selectbox("Amount", options=ic_columns, index=default_index(ic_columns, default_ic_amount), key="ic_amount")
    ic_ref_primary   = st.selectbox("Reference (primary)", options=ic_columns, index=default_index(ic_columns, default_ic_ref_a), key="ic_ref_primary")
    ic_ref_fallback_options = ["(none)"] + ic_columns
    ic_ref_fallback  = st.selectbox("Reference (fallback)", options=ic_ref_fallback_options, index=default_index(ic_ref_fallback_options, default_ic_ref_b), key="ic_ref_fallback")
    ic_desc_cols = st.multiselect("Description columns (tie-break)", options=ic_columns, default=[c for c in default_ic_descs if c in ic_columns], key="ic_desc_cols")

with col2:
    st.subheader("Moniepoint")
    b_date   = st.selectbox("Date", options=b_columns, index=default_index(b_columns, default_b_date), key="b_date")
    b_ref    = st.selectbox("Reference", options=b_columns, index=default_index(b_columns, default_b_ref), key="b_ref")
    b_desc   = st.selectbox("Narration / Description", options=b_columns, index=default_index(b_columns, default_b_desc), key="b_desc")

    st.markdown("**Bank Amount Source**")
    bank_amount_source = st.radio(
        "Choose which bank amount to match against iClass",
        ["Gross: Amount Paid", "Net: Settlement Credit (NGN)", "Net: Amount Paid - Charge (NGN)"],
        index=0,
        label_visibility="collapsed",
    )
    # let the user map columns that those choices rely on
    b_credit = st.selectbox("Column: Settlement Credit (NGN)", options=b_columns, index=default_index(b_columns, default_b_credit), key="b_credit")
    b_amtpaid = st.selectbox("Column: Amount Paid (gross)", options=b_columns, index=default_index(b_columns, default_b_amtpaid), key="b_amtpaid")
    b_charge = st.selectbox("Column: Charge (NGN)", options=["(none)"] + b_columns, index=default_index(["(none)"] + b_columns, default_b_charge), key="b_charge")

st.divider()

# ======================= Reconciliation Core =======================
def run_recon(ic_df, bk_df, mapping, date_window=1, amount_tol=0.0, require_exact_ref=False, enrich_ref=True, bank_amount_source="Gross: Amount Paid"):
    # --- iClass standardize ---
    ic_amount = mapping["ic_amount"]; ic_date = mapping["ic_date"]
    ic_ref_1 = mapping["ic_ref_primary"]; ic_ref_2 = mapping.get("ic_ref_fallback")
    ic_desc_cols = mapping.get("ic_desc_cols", [])

    ic_ref = ic_df[ic_ref_1].apply(norm_ref) if ic_ref_1 else None
    if ic_ref_2 and ic_ref_2 != "(none)":
        ic_ref = ic_ref.fillna(ic_df[ic_ref_2].apply(norm_ref))

    ic_desc = None
    if ic_desc_cols:
        ic_desc = ic_df[ic_desc_cols[0]].astype(str)
        for c in ic_desc_cols[1:]:
            ic_desc = ic_desc + " | " + ic_df[c].astype(str)

    i_std = pd.DataFrame({
        "i_row_id": ic_df.index,
        "i_amount": ic_df[ic_amount].apply(to_amount),
        "i_date":   ic_df[ic_date].apply(to_date),
        "i_ref":    ic_ref if ic_ref is not None else None,
        "i_desc":   ic_desc if ic_desc is not None else None,
        "i_success": True  # treating iClass list as candidates; verification is by bank presence
    })

    # --- bank standardize ---
    # decide b_amount based on user's choice
    choice = bank_amount_source
    if choice == "Net: Settlement Credit (NGN)":
        b_amount = bk_df[mapping["b_credit"]].apply(to_amount)
    elif choice == "Gross: Amount Paid":
        b_amount = bk_df[mapping["b_amtpaid"]].apply(to_amount)
    else:  # "Net: Amount Paid - Charge (NGN)"
        gross = bk_df[mapping["b_amtpaid"]].apply(to_amount)
        charge = bk_df[mapping["b_charge"]].apply(to_amount) if mapping["b_charge"] != "(none)" and mapping["b_charge"] in bk_df.columns else 0.0
        b_amount = gross - charge

    b_std = pd.DataFrame({
        "b_row_id": bk_df.index,
        "b_amount": b_amount,
        "b_date":   bk_df[mapping["b_date"]].apply(to_date),
        "b_ref":    bk_df[mapping["b_ref"]].apply(norm_ref),
        "b_desc":   bk_df[mapping["b_desc"]].astype(str)
    })
    # strictly keep positive inflows
    b_std = b_std[b_std["b_amount"] > 0].copy()
    if enrich_ref:
        b_std.loc[b_std["b_ref"].isna(), "b_ref"] = b_std["b_desc"].apply(extract_ref_from_text)

    # bucketed amounts for equality joins
    i_succ = i_std.copy()
    b_std = b_std.copy()
    i_succ["i_amount_r"] = i_succ["i_amount"].round(2)
    b_std["b_amount_r"] = b_std["b_amount"].round(2)

    # --- candidates ---
    # expand dates by window
    exp_rows = []
    for _, r in i_succ.iterrows():
        for dv in date_variants(r["i_date"], date_window):
            exp_rows.append((r["i_row_id"], r["i_amount_r"], r["i_ref"], r["i_desc"], r["i_date"], dv))
    exp = pd.DataFrame(exp_rows, columns=["i_row_id","i_amount_r","i_ref","i_desc","i_date","i_date_block"])

    if amount_tol <= 1e-9:
        # strict amount equality -> fast merge
        cand_amt_date = exp.merge(
            b_std[["b_row_id","b_amount_r","b_date","b_desc","b_ref"]],
            left_on=["i_amount_r","i_date_block"],
            right_on=["b_amount_r","b_date"],
            how="inner"
        )
    else:
        # allow amount tolerance: merge on date only then filter by |amount diff| <= tol
        cand_amt_date = exp.merge(
            b_std[["b_row_id","b_amount","b_amount_r","b_date","b_desc","b_ref"]],
            left_on=["i_date_block"],
            right_on=["b_date"],
            how="inner"
        )
        cand_amt_date["i_amount"] = cand_amt_date["i_amount_r"]
        cand_amt_date = cand_amt_date[np.abs(cand_amt_date["i_amount"] - cand_amt_date["b_amount"]) <= float(amount_tol) + 1e-9]
    cand_amt_date["rule"] = f"amt+date±{date_window}d"

    # exact ref matches
    cand_ref_exact = pd.DataFrame(columns=cand_amt_date.columns)
    if "i_ref" in i_succ.columns and "b_ref" in b_std.columns:
        left = i_succ.dropna(subset=["i_ref"]).copy()
        right = b_std.dropna(subset=["b_ref"]).copy()
        if not left.empty and not right.empty:
            c2 = left.merge(
                right[["b_row_id","b_date","b_desc","b_ref","b_amount","b_amount_r"]],
                left_on="i_ref", right_on="b_ref", how="inner"
            )
            if not c2.empty:
                cand_ref_exact = c2[["i_row_id","i_amount_r","i_date","i_ref","i_desc","b_row_id","b_date","b_desc","b_ref","b_amount","b_amount_r"]].copy()
                cand_ref_exact["rule"] = "ref=exact"

    # ref appears in description
    cand_ref_desc = pd.DataFrame(columns=cand_amt_date.columns)
    if "i_ref" in i_succ.columns and "b_desc" in b_std.columns:
        tmp = b_std.copy()
        tmp["_desc_alnum"] = tmp["b_desc"].apply(lambda d: re.sub(r"[^A-Za-z0-9]","", str(d)).upper() if pd.notna(d) else "")
        rows = []
        for _, r in i_succ.dropna(subset=["i_ref"]).iterrows():
            ref = r["i_ref"]
            mask = tmp["_desc_alnum"].str.contains(ref, na=False)
            sub = tmp[mask]
            if not sub.empty:
                t = sub.assign(i_row_id=r["i_row_id"], i_amount_r=r["i_amount_r"], i_date=r["i_date"], i_ref=r["i_ref"], i_desc=r["i_desc"])
                rows.append(t[["i_row_id","i_amount_r","i_date","i_ref","i_desc","b_row_id","b_date","b_desc","b_ref","b_amount","b_amount_r"]])
        if rows:
            cand_ref_desc = pd.concat(rows, ignore_index=True)
            cand_ref_desc["rule"] = "ref∈desc"

    # unify candidates
    cand_all = pd.concat([cand_amt_date, cand_ref_exact, cand_ref_desc], ignore_index=True).drop_duplicates()
    if cand_all.empty:
        recon = i_succ.copy()
        recon["matched"] = False
        recon["validation"] = np.where(recon["i_success"], "⚠️ success not found on bank", "—")
        return recon, b_std, pd.DataFrame(), b_std

    # decorate + score
    cand_all = cand_all.merge(i_succ[["i_row_id","i_amount","i_date","i_desc"]], on=["i_row_id","i_date"], how="left") \
                       .merge(b_std[["b_row_id","b_amount","b_date","b_desc"]], on=["b_row_id","b_date","b_desc"], how="left", suffixes=("", "_b"))
    cand_all["amt_diff"] = (cand_all["i_amount"] - cand_all["b_amount"]).abs()
    cand_all["date_delta_days"] = (cand_all["b_date"] - cand_all["i_date"]).abs().dt.days
    cand_all["desc_sim"] = cand_all.apply(lambda r: text_sim(r.get("i_desc_x"), r.get("b_desc")), axis=1)

    # filter by tolerance if we were in equality mode earlier (keeps logic consistent)
    if amount_tol > 1e-9 and "amt_diff" in cand_all:
        cand_all = cand_all[cand_all["amt_diff"] <= float(amount_tol) + 1e-9]

    # base score
    cand_all["score"] = 10*cand_all["amt_diff"] + 2*cand_all["date_delta_days"] - 5*cand_all["desc_sim"]
    # big bonus for strong ref signals
    ref_equal = (cand_all["i_ref"].notna()) & (cand_all["b_ref"].notna()) & (cand_all["i_ref"] == cand_all["b_ref"])
    ref_in_desc = cand_all["rule"].eq("ref∈desc")
    cand_all.loc[ref_equal, "score"] -= 1000.0
    cand_all.loc[ref_in_desc, "score"] -= 150.0

    # strict mode
    if require_exact_ref:
        cand_all = cand_all[ref_equal]

    if cand_all.empty:
        recon = i_succ.copy()
        recon["matched"] = False
        recon["validation"] = np.where(recon["i_success"], "⚠️ success not found on bank", "—")
        return recon, b_std, pd.DataFrame(), b_std

    # greedy one-to-one assignment
    cand_all = cand_all.sort_values(["score","i_row_id","b_row_id"]).reset_index(drop=True)
    used_i, used_b = set(), set()
    chosen = []
    for _, row in cand_all.iterrows():
        i_id = int(row["i_row_id"])
        b_id = int(row["b_row_id"])
        if i_id in used_i or b_id in used_b:
            continue
        chosen.append(row)
        used_i.add(i_id); used_b.add(b_id)
    best = pd.DataFrame(chosen) if chosen else pd.DataFrame(columns=cand_all.columns)

    recon = i_succ[["i_row_id","i_date","i_amount","i_ref","i_desc"]].copy()
    if not best.empty:
        recon = recon.merge(best[["i_row_id","b_row_id","b_date","b_amount","b_desc","b_ref","rule","amt_diff","date_delta_days","desc_sim","score"]],
                            on="i_row_id", how="left")
    recon["matched"] = recon["b_row_id"].notna()
    recon["validation"] = np.where(recon["matched"], "✅ matched success", "⚠️ success not found on bank")

    matched_bank_ids = set(recon["b_row_id"].dropna().astype(int).unique())
    unmatched_bank = b_std[~b_std["b_row_id"].isin(matched_bank_ids)].copy()
    unmatched_iclass = recon[~recon["matched"]].copy()
    return recon, b_std, unmatched_iclass, unmatched_bank

# ======================= Run & Display =======================
if run_btn:
    if ic_df is None or bk_df is None:
        st.error("Please upload both CSVs first.")
    else:
        mapping = {
            "ic_date": ic_date, "ic_amount": ic_amount,
            "ic_ref_primary": ic_ref_primary, "ic_ref_fallback": ic_ref_fallback,
            "ic_desc_cols": ic_desc_cols,
            "b_date": b_date, "b_credit": b_credit, "b_ref": b_ref, "b_desc": b_desc,
            "b_amtpaid": b_amtpaid, "b_charge": b_charge
        }
        recon, b_std, unmatched_iclass, unmatched_bank = run_recon(
            ic_df, bk_df, mapping,
            date_window=int(date_window),
            amount_tol=float(amount_tol),
            require_exact_ref=require_exact_ref,
            enrich_ref=enrich_ref_from_bank_narr,
            bank_amount_source=bank_amount_source
        )

        # -------- Row metrics --------
        colA, colB, colC = st.columns(3)
        with colA:
            st.metric("Total iClass rows", len(recon))
            st.metric("Matched successes", int(recon["matched"].sum()))
        with colB:
            st.metric("Unmatched successes", int((~recon["matched"]).sum()))
            st.metric("Bank credit lines (positive)", int(b_std.shape[0]))
        with colC:
            st.metric("Unused bank credits", int(unmatched_bank.shape[0]))
            st.metric("Date window (± days)", date_window)

        # -------- Amount metrics --------
        total_iclass_amt      = float(np.nansum(recon["i_amount"]))
        matched_iclass_amt    = float(np.nansum(recon.loc[recon["matched"], "i_amount"]))
        unmatched_iclass_amt  = float(np.nansum(recon.loc[~recon["matched"], "i_amount"]))

        total_bank_credit_amt = float(np.nansum(b_std["b_amount"]))
        matched_bank_amt      = float(np.nansum(recon.loc[recon["matched"], "b_amount"]))
        unused_bank_amt       = float(np.nansum(unmatched_bank["b_amount"]))

        st.markdown("### Amount summaries")
        r1c1, r1c2, r1c3 = st.columns(3)
        r1c1.metric("Total iClass amount",   money(total_iclass_amt))
        r1c2.metric("Matched iClass amount", money(matched_iclass_amt))
        r1c3.metric("Unmatched iClass amount", money(unmatched_iclass_amt))

        r2c1, r2c2, r2c3 = st.columns(3)
        r2c1.metric("Total bank credits amount", money(total_bank_credit_amt))
        r2c2.metric("Matched bank credits amount", money(matched_bank_amt),
                    delta=money(matched_iclass_amt - matched_bank_amt))
        r2c3.metric("Unused bank credits amount", money(unused_bank_amt))

        with st.expander("Amounts summary (table)"):
            amt_tbl = pd.DataFrame({
                "Metric": [
                    "Total iClass amount",
                    "Matched iClass amount",
                    "Unmatched iClass amount",
                    "Total bank credits amount",
                    "Matched bank credits amount",
                    "Unused bank credits amount",
                    "Diff (matched iClass − matched bank)"
                ],
                "Value": [
                    money(total_iclass_amt),
                    money(matched_iclass_amt),
                    money(unmatched_iclass_amt),
                    money(total_bank_credit_amt),
                    money(matched_bank_amt),
                    money(unused_bank_amt),
                    money(matched_iclass_amt - matched_bank_amt),
                ]
            })
            st.dataframe(amt_tbl, use_container_width=True)

        # -------- Reconciled table + filters --------
        st.subheader("Reconciled iClass")
        show_matches_only = st.checkbox("Show matches only", value=False)
        view_cols = [
            "i_date","i_amount","i_ref","i_desc",
            "b_date","b_amount","b_ref","b_desc",
            "rule","amt_diff","date_delta_days","desc_sim","score","validation"
        ]
        recon_view = recon[view_cols] if all(c in recon.columns for c in view_cols) else recon
        if show_matches_only and "matched" in recon.columns:
            recon_view = recon_view[recon["matched"]]
        st.dataframe(recon_view.head(2000), use_container_width=True)

        with st.expander("Unmatched iClass (needs investigation)"):
            st.dataframe(unmatched_iclass.head(2000), use_container_width=True)
        with st.expander("Unused Bank Credits"):
            st.dataframe(unmatched_bank.head(2000), use_container_width=True)

        # -------- Daily totals reconciliation (batch view) --------
        st.subheader("Daily Totals Reconciliation")
        daily_i = (
            recon.assign(day=recon["i_date"])
                 .groupby("day", as_index=False)["i_amount"].sum()
                 .rename(columns={"i_amount": "iclass_total"})
        )
        daily_b = (
            b_std.groupby("b_date", as_index=False)["b_amount"].sum()
                 .rename(columns={"b_amount": "bank_total", "b_date": "day"})
        )
        daily = daily_i.merge(daily_b, on="day", how="outer").sort_values("day")
        daily["iclass_total"] = daily["iclass_total"].fillna(0.0).round(2)
        daily["bank_total"]   = daily["bank_total"].fillna(0.0).round(2)
        daily["diff"]         = (daily["iclass_total"] - daily["bank_total"]).round(2)
        st.dataframe(daily, use_container_width=True)

        # -------- Downloads --------
        output = io.BytesIO()
        with pd.ExcelWriter(output, engine="xlsxwriter") as writer:
            recon.to_excel(writer, sheet_name="reconciled_iclass", index=False)
            unmatched_iclass.to_excel(writer, sheet_name="unmatched_iclass_success", index=False)
            unmatched_bank.to_excel(writer, sheet_name="unmatched_bank_credit", index=False)

            # Write a tiny config sheet for auditability
            cfg = pd.DataFrame({
                "Parameter": [
                    "Date window (±days)", "Amount tolerance (₦)", "Require exact ref",
                    "Extract ref from narration", "Bank amount source",
                    "iClass Date", "iClass Amount", "iClass Ref (primary)", "iClass Ref (fallback)", "iClass Desc cols",
                    "Bank Date", "Bank Ref", "Bank Desc", "Settlement Credit col", "Amount Paid col", "Charge col"
                ],
                "Value": [
                    date_window, amount_tol, require_exact_ref,
                    enrich_ref_from_bank_narr, bank_amount_source,
                    ic_date, ic_amount, ic_ref_primary, ic_ref_fallback, ", ".join(ic_desc_cols) if ic_desc_cols else "",
                    b_date, b_ref, b_desc, b_credit, b_amtpaid, b_charge
                ]
            })
            cfg.to_excel(writer, sheet_name="config", index=False)

        st.download_button("⬇️ Download Excel (3 sheets + config)",
                           data=output.getvalue(),
                           file_name="reconciliation_output.xlsx",
                           mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

        st.download_button("⬇️ Download reconciled_iclass.csv",
                           data=recon.to_csv(index=False).encode("utf-8-sig"),
                           file_name="reconciled_iclass.csv", mime="text/csv")
        st.download_button("⬇️ Download unmatched_iclass_success.csv",
                           data=unmatched_iclass.to_csv(index=False).encode("utf-8-sig"),
                           file_name="unmatched_iclass_success.csv", mime="text/csv")
        st.download_button("⬇️ Download unmatched_bank_credit.csv",
                           data=unmatched_bank.to_csv(index=False).encode("utf-8-sig"),
                           file_name="unmatched_bank_credit.csv", mime="text/csv")
else:
    st.info("Upload your two CSVs in the sidebar, confirm column mapping, tweak parameters, then click **Run Reconciliation**.")
