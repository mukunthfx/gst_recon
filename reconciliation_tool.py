import pandas as pd
import streamlit as st
from datetime import datetime
import re
import io

# Function to extract numeric part from invoice number
def extract_numeric_part(invoice_number):
    if pd.isna(invoice_number):
        return ""
    numeric_part = re.findall(r'\d+', str(invoice_number))
    return "".join(numeric_part) if numeric_part else ""

# Function to preprocess data
def preprocess_data(df):
    df['GST Number'] = df['GST Number'].astype(str).replace('nan', '')
    if 'Invoice Number' in df.columns:
        df['Invoice Numeric'] = df['Invoice Number'].apply(extract_numeric_part)
    else:
        df['Invoice Numeric'] = ""
    for col in ['CGST', 'IGST', 'SGST']:
        df[col] = df[col].fillna(0).astype(float)
    return df


# Function to reconcile data
def reconcile_data(books_df, portal_df, tolerance=5, progress_bar=None):
    books_df = books_df.drop_duplicates()
    portal_df = portal_df.drop_duplicates()

    books_df.sort_values(by=['GST Number', 'Invoice Numeric'], inplace=True)
    portal_df.sort_values(by=['GST Number', 'Invoice Numeric'], inplace=True)

    matched = []
    processed_book_keys = set()
    matched_portal_indices = set()
    matched_values_portal_indices = set()
    total_books = len(books_df)

    for idx, (_, book_row) in enumerate(books_df.iterrows()):
        if progress_bar:
            progress_bar.progress((idx + 1) / total_books, text="Processing book entries...")

        gst_number = book_row['GST Number']
        invoice_numeric = book_row['Invoice Numeric']
        taxable_value = book_row['Taxable Value']
        igst = book_row['IGST']
        cgst = book_row['CGST']
        sgst = book_row['SGST']
        client_name = book_row.get('Client Name', '')  # Get client name from books

        book_key = f"{gst_number}_{invoice_numeric}_{taxable_value}"
        if book_key in processed_book_keys:
            continue

        potential_matches = portal_df[
            (portal_df['GST Number'] == gst_number) &
            (portal_df['Invoice Numeric'] == invoice_numeric) &
            (~portal_df.index.isin(matched_portal_indices)) &
            (~portal_df.index.isin(matched_values_portal_indices))
            ]

        if not potential_matches.empty:
            portal_row = potential_matches.iloc[0]
            portal_idx = portal_row.name

            value_match = (
                    abs(taxable_value - portal_row['Taxable Value']) <= tolerance and
                    abs(igst - portal_row['IGST']) <= tolerance and
                    abs(cgst - portal_row['CGST']) <= tolerance and
                    abs(sgst - portal_row['SGST']) <= tolerance
            )

            match_type = 'Value Match Only' if value_match else 'Exact Match'

            # Create flattened entry for matched results
            matched_entry = {
                'Match Type': match_type,
                # Book entries
                'Book_Index': book_row['Index'],
                'Book_Client Name': book_row.get('Client Name', ''),
                'Book_Invoice No': book_row.get('Invoice No', ''),
                'Book_Invoice Date': book_row.get('Invoice Date', ''),
                'Book_Taxable Value': book_row['Taxable Value'],
                'Book_IGST': book_row['IGST'],
                'Book_CGST': book_row['CGST'],
                'Book_SGST': book_row['SGST'],
                # Portal entries
                'Portal_Index': portal_row['Index'],
                'Portal_Client Name': portal_row.get('Client Name', ''),  # trying to get client name from portal
                'Portal_Invoice No': portal_row.get('Invoice No', ''),
                'Portal_Invoice Date': portal_row.get('Invoice Date', ''),
                'Portal_Taxable Value': portal_row['Taxable Value'],
                'Portal_IGST': portal_row['IGST'],
                'Portal_CGST': portal_row['CGST'],
                'Portal_SGST': portal_row['SGST'],
            }

            matched.append(matched_entry)

            if value_match:
                matched_values_portal_indices.add(portal_idx)
            else:
                matched_portal_indices.add(portal_idx)

            processed_book_keys.add(book_key)

    # Add Match Type to portal_df
    portal_df['Match Type'] = 'Unmatched'
    portal_df.loc[list(matched_portal_indices), 'Match Type'] = 'Exact Match'
    portal_df.loc[list(matched_values_portal_indices), 'Match Type'] = 'Value Match Only'

    unmatched_books = books_df[~books_df.index.isin([m['Book_Index'] for m in matched])]
    unmatched_portal = portal_df[portal_df['Match Type'] == 'Unmatched']

    return (
        pd.DataFrame(matched),
        unmatched_books,
        portal_df,
        unmatched_portal
    )


# Function to calculate summary metrics
def calculate_summary(books_df, portal_df, matched_df, unmatched_books_df, portal_df_annotated):
    total_matched = len(matched_df)
    total_exact = sum(matched_df['Match Type'] == 'Exact Match')
    total_value = sum(matched_df['Match Type'] == 'Value Match Only')

    summary = {
        "Total Entries (Books)": len(books_df),
        "Total Entries (Portal)": len(portal_df),
        "Total Matched (Exact)": total_exact,
        "Total Matched (Value Only)": total_value,
        "Total Unmatched (Books)": len(unmatched_books_df),
        "Total Unmatched (Portal)": len(portal_df_annotated[portal_df_annotated['Match Type'] == 'Unmatched']),
        "Taxable Value (Books)": books_df['Taxable Value'].sum(),
        "Taxable Value (Portal)": portal_df['Taxable Value'].sum(),
        "Taxable Value Difference": books_df['Taxable Value'].sum() - portal_df['Taxable Value'].sum(),
        "Total Tax Difference (IGST)": books_df['IGST'].sum() - portal_df['IGST'].sum(),
        "Total Tax Difference (CGST)": books_df['CGST'].sum() - portal_df['CGST'].sum(),
        "Total Tax Difference (SGST)": books_df['SGST'].sum() - portal_df['SGST'].sum(),
    }

    # Add percentages
    summary["Match Rate (Exact)"] = f"{(total_exact / len(books_df) * 100):.1f}%" if len(books_df) > 0 else "N/A"
    summary["Match Rate (Value)"] = f"{(total_value / len(books_df) * 100):.1f}%" if len(books_df) > 0 else "N/A"

    return summary


# Streamlit App
def main():
    st.set_page_config(page_title="GST Recon", page_icon="📊", layout="wide")
    st.title("📊 Advanced GST Reconciliation Tool")
    st.markdown("### Compare your books with GST portal data")

    with st.sidebar:
        st.header("Configuration")
        tolerance = st.number_input("Value Tolerance (±)",
                                    min_value=0.0,
                                    max_value=1000.0,
                                    value=5.0,
                                    help="Allowed difference for value matches")

        st.markdown("---")
        st.markdown("**Instructions:**")
        st.markdown("1. Upload Books and Portal data\n2. Configure tolerance\n3. View results\n4. Export if needed")

    upload_col1, upload_col2 = st.columns(2)
    with upload_col1:
        books_file = st.file_uploader("Upload Books Data", type=["xlsx"],
                                      help="Should contain GST Number, Taxable Value, and tax columns.  Also include 'Client Name' column")
    with upload_col2:
        portal_file = st.file_uploader("Upload Portal Data", type=["xlsx"],
                                       help="Should match Books file structure.  Ideally include 'Client Name' column")

    if books_file and portal_file:
        try:
            with st.spinner("Loading data..."):
                books_df = pd.read_excel(books_file).reset_index(names=['Index'])
                portal_df = pd.read_excel(portal_file).reset_index(names=['Index'])

            # Validation
            required_columns = ['GST Number', 'Taxable Value', 'IGST', 'CGST', 'SGST', 'Client Name']
            missing_books = [col for col in required_columns if col not in books_df.columns]
            missing_portal = [col for col in required_columns if col not in portal_df.columns]

            if missing_books or missing_portal:
                error_msg = []
                if missing_books:
                    error_msg.append(f"**Books file** missing: {', '.join(missing_books)}")
                if missing_portal:
                    error_msg.append(f"**Portal file** missing: {', '.join(missing_portal)}")
                st.error("\n\n".join(error_msg))
                st.stop()

            with st.spinner("Preprocessing data..."):
                books_df = preprocess_data(books_df)
                portal_df = preprocess_data(portal_df)

            progress_bar = st.progress(0, text="Starting reconciliation...")
            with st.spinner("Matching entries..."):
                matched_df, unmatched_books_df, portal_df_annotated, unmatched_portal_df = reconcile_data(
                    books_df, portal_df, tolerance, progress_bar
                )
            progress_bar.empty()

            # Generate summary
            summary = calculate_summary(books_df, portal_df, matched_df,
                                        unmatched_books_df, portal_df_annotated)

            # Show summary
            st.markdown("---")
            st.subheader("Reconciliation Summary")
            cols = st.columns(4)
            metric_keys = [
                ['Total Entries (Books)', 'Total Entries (Portal)'],
                ['Total Matched (Exact)', 'Total Matched (Value Only)'],
                ['Total Unmatched (Books)', 'Total Unmatched (Portal)'],
                ['Match Rate (Exact)', 'Match Rate (Value)']
            ]

            for col, metrics in zip(cols, metric_keys):
                with col:
                    for metric in metrics:
                        value = summary.get(metric, 'N/A')
                        st.metric(label=metric.replace("_", " ").title(),
                                  value=value)

            # Detailed results tabs
            tab1, tab2, tab3, tab4 = st.tabs(
                ["🧩 Matched Entries", "📦 Unmatched Books", "📭 Unmatched Portal", "📊 Full Analysis"]
            )

            with tab1:
                st.dataframe(
                    matched_df[
                        ['Match Type',
                         'Book_Client Name',
                         'Book_Invoice No', 'Book_Invoice Date', 'Book_Taxable Value',
                         'Portal_Client Name',
                         'Portal_Invoice No', 'Portal_Invoice Date', 'Portal_Taxable Value',
                         'Book_IGST', 'Portal_IGST', 'Book_CGST', 'Portal_CGST',
                         'Book_SGST', 'Portal_SGST']
                    ].style.format({
                        'Book_Taxable Value': '{:.2f}',
                        'Portal_Taxable Value': '{:.2f}',
                        'Book_IGST': '{:.2f}',
                        'Portal_IGST': '{:.2f}',
                        'Book_CGST': '{:.2f}',
                        'Portal_CGST': '{:.2f}',
                        'Book_SGST': '{:.2f}',
                        'Portal_SGST': '{:.2f}'
                    }),
                    height=400
                )

            with tab2:
                st.dataframe(unmatched_books_df[
                    ['Client Name', 'Invoice No', 'Invoice Date', 'GST Number',
                     'Taxable Value', 'IGST', 'CGST', 'SGST']
                ].style.format({
                    'Taxable Value': '{:.2f}',
                    'IGST': '{:.2f}',
                    'CGST': '{:.2f}',
                    'SGST': '{:.2f}'
                }), height=400)

            with tab3:
                st.dataframe(unmatched_portal_df[
                    ['Client Name', 'Invoice No', 'Invoice Date', 'GST Number',
                     'Taxable Value', 'IGST', 'CGST', 'SGST']
                ].style.format({
                    'Taxable Value': '{:.2f}',
                    'IGST': '{:.2f}',
                    'CGST': '{:.2f}',
                    'SGST': '{:.2f}'
                }), height=400)

            with tab4:
                st.markdown("#### Tax Summary Comparison")
                analysis_cols = st.columns(2)

                with analysis_cols[0]:
                    st.markdown("**Books Data**")
                    st.dataframe(books_df[['Taxable Value', 'IGST', 'CGST', 'SGST']].sum().to_frame(name='Total'))

                with analysis_cols[1]:
                    st.markdown("**Portal Data**")
                    st.dataframe(portal_df[['Taxable Value', 'IGST', 'CGST', 'SGST']].sum().to_frame(name='Total'))

                st.markdown(f"**Key Differences:**")
                st.markdown(f"- Taxable Value Difference: ₹{summary['Taxable Value Difference']:,.2f}")
                st.markdown(f"- Total IGST Difference: ₹{summary['Total Tax Difference (IGST)']:,.2f}")
                st.markdown(f"- Total CGST Difference: ₹{summary['Total Tax Difference (CGST)']:,.2f}")
                st.markdown(f"- Total SGST Difference: ₹{summary['Total Tax Difference (SGST)']:,.2f}")

            # Export functionality
            st.markdown("---")
            with st.expander("📤 Export Results"):
                buffer = io.BytesIO()
                with pd.ExcelWriter(buffer) as writer:
                    matched_df.to_excel(writer, sheet_name="Matched", index=False)
                    unmatched_books_df.to_excel(writer, sheet_name="Unmatched Books", index=False)
                    unmatched_portal_df.to_excel(writer, sheet_name="Unmatched Portal", index=False)
                    portal_df_annotated.to_excel(writer, sheet_name="Full Portal Data", index=False)

                st.download_button(
                    label="Download Full Report",
                    data=buffer.getvalue(),
                    file_name=f"GST_Reconciliation_{datetime.now().strftime('%Y%m%d')}.xlsx",
                    mime="application/vnd.ms-excel"
                )

            st.success("Reconciliation complete! ✅")

        except Exception as e:
            st.error(f"🚨 Error: {str(e)}")
            st.stop()


if __name__ == "__main__":
    main()
