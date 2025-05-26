import streamlit as st
import pandas as pd
import geopandas as gpd
from shapely.geometry import Point
import zipfile
import os
import folium
from streamlit_folium import folium_static
from folium.plugins import MarkerCluster
from st_aggrid import AgGrid, GridOptionsBuilder, JsCode
import fiona
import tempfile
import datetime
from PIL import Image
import io
import altair as alt
import streamlit.components.v1 as components
import pydeck as pdk
import plotly.express as px
import leafmap.foliumap as leafmap
from pandas.tseries.offsets import MonthEnd
import locale

# Set locale to Indonesian for month names
try:
    locale.setlocale(locale.LC_TIME, 'id_ID')
except locale.Error:
    locale.setlocale(locale.LC_TIME, '')  # Fallback to default locale if id_ID is unavailable

# Konfigurasi halaman Streamlit
st.set_page_config(page_title="Asuransi Banjir Askrindo", page_icon="assets/Logo Askrindo (Kotak).jpeg", layout="wide")
st.logo("assets/Logo Askrindo BUMN.png", icon_image="assets/Logo Askrindo BUMN.png")
st.title("🌊 Web Application Flood Insurance Askrindo")

st.write("##### Untuk memahami Dashboard secara keseluruhan dapat mengakses link https://drive.google.com/file/d/15ehrqGegyiQHTNk_TV6bZ45BkPusBhOA/view?usp=sharing")
st.write("##### Data dapat diakses melalui link https://bit.ly/FileUploadDashboardAsuransiBanjir")

# Step 1: Upload CSV
st.subheader("⬆️ Upload Data yang Diperlukan")
csv_file = st.file_uploader("📄 Upload CSV", type=["csv"])

if csv_file:
    # Membaca file CSV
    df = pd.read_csv(csv_file)
    df.columns = df.columns.str.strip()  # Bersihkan spasi pada nama kolom

    # Display "as of" date based on the last day of the month of the latest INCEPTION DATE
    if 'INCEPTION DATE' in df.columns:
        if not pd.api.types.is_datetime64_any_dtype(df['INCEPTION DATE']):
            df['INCEPTION DATE'] = pd.to_datetime(df['INCEPTION DATE'], format='mixed', dayfirst=True, errors='coerce')
        # Get the latest date from INCEPTION DATE
        latest_date = df['INCEPTION DATE'].max()
        if pd.notna(latest_date):
            last_day_of_month = (latest_date + MonthEnd(0)).date()
            as_of_date = last_day_of_month.strftime('%d %B %Y')
            st.info(f"ℹ️ Data yang diupload adalah data as of **{as_of_date}**")
        else:
            st.info("ℹ️ Data yang diupload tidak memiliki tanggal valid pada kolom `INCEPTION DATE`")
    else:
        st.info("ℹ️ Kolom `INCEPTION DATE` tidak ditemukan, tidak bisa menampilkan data as of")

    # Step 2: Proses EXPIRY DATE tanpa menghapus baris
    if 'EXPIRY DATE' in df.columns:
        # Fungsi untuk mencoba beberapa format tanggal
        def parse_dates(date_str):
            if pd.isna(date_str):
                return date_str  # Biarkan NaN tetap
            try:
                # Coba format DD/MM/YYYY
                return pd.to_datetime(date_str, format='%d/%m/%Y', errors='raise')
            except ValueError:
                try:
                    # Coba format MM/DD/YYYY
                    return pd.to_datetime(date_str, format='%m/%d/%Y', errors='raise')
                except ValueError:
                    # Jika gagal, kembalikan string asli dan tandai untuk peringatan
                    return date_str

        # Terapkan parsing tanggal
        df['EXPIRY DATE'] = df['EXPIRY DATE'].apply(parse_dates)

        # Identifikasi baris dengan tanggal yang tidak valid (masih berupa string)
        invalid_dates = df[df['EXPIRY DATE'].apply(lambda x: isinstance(x, str) and not pd.isna(x))]
        if not invalid_dates.empty:
            st.warning(f"⚠️ Terdapat {len(invalid_dates)} baris dengan EXPIRY DATE tidak valid: {invalid_dates['EXPIRY DATE'].unique().tolist()[:5]}")
            # Opsional: Simpan baris bermasalah untuk analisis
            invalid_dates.to_csv('invalid_expiry_dates.csv', index=False)
        
        # Konversi ke date hanya untuk yang sudah datetime
        df['EXPIRY DATE'] = df['EXPIRY DATE'].apply(lambda x: x.date() if isinstance(x, pd.Timestamp) else x)
        
        # Pilih Full Data atau Inforce Only
        st.markdown("### 🔍 Pilih Tipe Data yang Ingin Dipakai")
        data_option = st.radio("Ingin menggunakan data yang mana?", ["Full Data", "Filter by Expiry Date"])

        if data_option == "Filter by Expiry Date":
            # Let user select a date
            selected_date = st.date_input("Pilih tanggal untuk filter EXPIRY DATE >", value=pd.to_datetime("2024-12-31").date())
            # Filter hanya untuk baris dengan EXPIRY DATE yang valid (datetime)
            filtered_df = df[df['EXPIRY DATE'].apply(lambda x: isinstance(x, pd.Timestamp) or isinstance(x, datetime.date))]
            filtered_df = filtered_df[filtered_df['EXPIRY DATE'] > selected_date]
            st.success(f"✅ Menggunakan data dengan **{len(filtered_df):,} baris** (EXPIRY DATE > {selected_date})")
            df = filtered_df  # Update df dengan hasil filter
        else:
            st.success(f"✅ Menggunakan **data full** dengan **{len(df):,} baris**")
    else:
        st.warning("⚠️ Kolom `EXPIRY DATE` tidak ditemukan, tidak bisa filter data inforce.")

    # Tampilkan dataframe setelah proses
    st.dataframe(df, use_container_width=True, hide_index=True)
    # Step 3: Upload shapefiles
    st.subheader("🗂 Upload Shapefile")
    shp_zips = st.file_uploader(
        "Upload Beberapa Shapefile (.zip). File zip ini harus terdiri atas .shp, .shx, .dbf, .prj, dsb",
        type=["zip"],
        accept_multiple_files=True
    )

    # Fungsi untuk membersihkan kolom koordinat
    def clean_coordinate_column(series):
        return (
            series.astype(str)
            .str.strip()
            .str.replace("–", "-", regex=False)
            .str.replace(",", ".", regex=False)
            .str.replace(r"[^0-9\.-]", "", regex=True)
        )

    image = Image.open("assets/Flowchart Asuransi Banjir.png")
    st.image(image, use_container_width=True)

    lon_col = "Longitude"
    lat_col = "Latitude"

    # Validasi kolom koordinat
    if lat_col in df.columns and lon_col in df.columns:
        df['Latitude'] = pd.to_numeric(clean_coordinate_column(df['Latitude']), errors='coerce')
        df['Longitude'] = pd.to_numeric(clean_coordinate_column(df['Longitude']), errors='coerce')

        lat_na = df['Latitude'].isna().sum()
        lon_na = df['Longitude'].isna().sum()

        if lat_na > 0 or lon_na > 0:
            st.warning(f"⚠️ Terdapat {lat_na} Latitude dan {lon_na} Longitude yang tidak valid setelah parsing & koreksi.")
            invalid_rows = df[df['Latitude'].isna() | df['Longitude'].isna()]
            st.dataframe(invalid_rows.head())

            invalid_csv = invalid_rows.to_csv(index=False).encode("utf-8")
            st.download_button(
                "⬇️ Unduh Baris Tidak Valid",
                data=invalid_csv,
                file_name="invalid_coordinates.csv",
                mime="text/csv"
            )
    else:
        st.error("Kolom 'Latitude' dan/atau 'Longitude' tidak ditemukan dalam data.")
        st.stop()

    # Proses shapefiles
    if shp_zips:
        gdf_points = gpd.GeoDataFrame(
            df.copy(),
            geometry=[Point(xy) for xy in zip(df[lon_col], df[lat_col])],
            crs="EPSG:4326"
        )

        joined_list = []
        for shp_zip in shp_zips:
            with tempfile.TemporaryDirectory() as tmpdir:
                with zipfile.ZipFile(shp_zip, 'r') as zip_ref:
                    zip_ref.extractall(tmpdir)

                shp_path = None
                for root, _, files in os.walk(tmpdir):
                    for file in files:
                        if file.endswith(".shp") and not file.startswith("._") and "__MACOSX" not in root:
                            shp_path = os.path.join(root, file)

                if not shp_path:
                    st.warning(f"Tidak ditemukan file .shp dalam ZIP: {shp_zip.name}")
                    continue

                try:
                    gdf_shape = gpd.read_file(shp_path)
                    gdf_shape.columns = gdf_shape.columns.str.strip()
                    gdf_points_proj = gdf_points.to_crs(gdf_shape.crs)
                    joined = gpd.sjoin(gdf_points_proj, gdf_shape, how="left", predicate="intersects")
                    joined_list.append(joined)
                except Exception as e:
                    st.error(f"Gagal memproses shapefile dari {shp_zip.name}: {e}")

        if joined_list:
            combined = pd.concat(joined_list)
            keywords = ['gridcode', 'hasil_gridcode', 'kode_grid']
            gridcode_cols = [col for col in combined.columns if any(kw in col.lower() for kw in keywords)]
            grid_col = gridcode_cols[0] if gridcode_cols else None

            if grid_col:
                combined = combined[[lon_col, lat_col, grid_col]].drop_duplicates(subset=[lon_col, lat_col])
            else:
                combined = combined[[lon_col, lat_col]].drop_duplicates()

            final = df.merge(combined, on=[lon_col, lat_col], how='left')

            if grid_col:
                final['Kategori Risiko'] = final[grid_col].map({1: 'Rendah', 2: 'Sedang', 3: 'Tinggi'}).fillna("No Risk")
            else:
                st.warning("⚠️ Tidak ditemukan kolom terkait 'gridcode'. Tidak dapat mengkategorikan risiko.")

            # Step 5: Persentase Estimasi Kerugian
            st.subheader("🧮 Persentase Estimasi Kerugian")
            st.markdown("""
                <div style='text-align: justify'>
                Kategori Okupasi terdiri atas Residensial, Industrial dan Komersial. Selain itu, Kategori Risiko akan memuat jumlah lantai dari bangunan dan letak isi yang ada di dalamnya. Untuk mengetahui acuan yang digunakan, dapat dilihat melalui tabel berikut.
                </div>
            """, unsafe_allow_html=True)

            # Data untuk tabel rate
            image = Image.open("assets/Estimated Loss.png")
            st.image(image)

            # Step 6: Hitung rate berdasarkan risiko dan okupasi
            if 'Kategori Risiko' in final.columns:
                building_col = "Kategori Okupasi"
                floor_col = "Jumlah Lantai"

                missing_cols = []
                if building_col not in final.columns:
                    missing_cols.append(building_col)
                if floor_col not in final.columns:
                    missing_cols.append(floor_col)
                if missing_cols:
                    st.error(f"Kolom berikut tidak ditemukan dalam data: {', '.join(missing_cols)}")
                    st.stop()

                final[floor_col] = pd.to_numeric(final[floor_col], errors='coerce')
                final[floor_col] = final[floor_col].apply(lambda x: 1 if x == 0 else x)

                rate_dict = {
                    'No Risk': {
                        'Residensial': {'1': 0.0, 'more_than_1': 0.0},
                        'Komersial': {'1': 0.0, 'more_than_1': 0.0},
                        'Industrial': {'1': 0.0, 'more_than_1': 0.0}
                    },
                    'Rendah': {
                        'Residensial': {'1': 0.15, 'more_than_1': 0.10},
                        'Komersial': {'1': 0.20, 'more_than_1': 0.15},
                        'Industrial': {'1': 0.10, 'more_than_1': 0.08}
                    },
                    'Sedang': {
                        'Residensial': {'1': 0.30, 'more_than_1': 0.20},
                        'Komersial': {'1': 0.35, 'more_than_1': 0.25},
                        'Industrial': {'1': 0.20, 'more_than_1': 0.15}
                    },
                    'Tinggi': {
                        'Residensial': {'1': 0.50, 'more_than_1': 0.35},
                        'Komersial': {'1': 0.55, 'more_than_1': 0.40},
                        'Industrial': {'1': 0.40, 'more_than_1': 0.30}
                    }
                }

                def lookup_rate(row):
                    try:
                        risk = row['Kategori Risiko']
                        okupasi = row[building_col]
                        floors = row[floor_col]
                        if pd.isna(floors):
                            return None
                        floors = int(floors)
                        floor_key = '1' if floors == 1 else 'more_than_1'
                        return rate_dict[risk][okupasi][floor_key]
                    except:
                        return None

                final['Scaling'] = final.apply(lookup_rate, axis=1)

            # Step 7: Hitung Probable Maximum Losses (PML)
            selected_rate = "Scaling"
            selected_tsi = "TSI IDR"

            if selected_rate not in final.columns or selected_tsi not in final.columns:
                st.error(f"Kolom {selected_rate} dan/atau {selected_tsi} tidak ditemukan dalam data.")
                st.stop()

            def clean_tsi_column(series):
                return pd.to_numeric(
                    series.astype(str).str.replace(r"[^\d]", "", regex=True),
                    errors='coerce'
                )

            final[selected_tsi] = clean_tsi_column(final[selected_tsi])
            final['PML'] = final[selected_tsi] * final[selected_rate]

            final["Kode Okupasi (2 digit awal)"] = final["Kode Okupasi_mod"].str[:2]
            final['Kode Okupasi (2 digit awal)'] = final['Kode Okupasi (2 digit awal)'].replace({
                '#V': '00',
                '#VALUE!': '00',
                'na': '00',
                'NaN': '00',
                '4,': '41',
                '4.': '41'
            })
            kolom_baru = final.pop("Kode Okupasi (2 digit awal)")
            pos = final.columns.get_loc("Kode Okupasi_mod") + 1
            final.insert(pos, "Kode Okupasi (2 digit awal)", kolom_baru)

            st.subheader("📈 Hasil Akhir")
            st.dataframe(final, use_container_width=True, hide_index=True)

            # Deteksi nama file berdasarkan nama file upload
            uploaded_filename = csv_file.name.lower()
            if "jakarta" in uploaded_filename:
                output_filename = "Data Banjir Jakarta - After Computation.csv"
            elif "all porto" in uploaded_filename:
                output_filename = "Data Banjir All Porto - After Computation.csv"
            else:
                output_filename = "Data Banjir - After Computation.csv"

            # Buat CSV untuk diunduh
            output_premi = io.StringIO()
            final.to_csv(output_premi, index=False, encoding='utf-8-sig')

            # Tombol unduh
            st.download_button(
                "⬇️ Unduh Hasil Akhir (.csv)",
                data=output_premi.getvalue(),
                file_name=output_filename,
                mime="text/csv"
            )

            uploaded_filename = csv_file.name.lower()
            if "jakarta" in uploaded_filename:
                output_fileexcel = "Data Banjir Jakarta - After Computation.xlsx"
            elif "all porto" in uploaded_filename:
                output_fileexcel = "Data Banjir All Porto - After Computation.xlsx"
            else:
                output_fileexcel = "Data Banjir - After Computation.xlsx"

            output_excel = io.BytesIO()
            with pd.ExcelWriter(output_excel, engine='xlsxwriter') as writer:
                df.to_excel(writer, index=False, sheet_name='Data')

            # Kembalikan posisi ke awal agar bisa dibaca
            output_excel.seek(0)

            # Tombol untuk mengunduh
            st.download_button(
                label="⬇️ Unduh Hasil Akhir (.xlsx)",
                data=output_excel,
                file_name=output_fileexcel,
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            )

            st.write("###### Untuk analisis lebih lanjut, maka dapat memanfaatkan Google Earth Pro. Untuk langkah-langkahnya dapat dilakukan sebagai berikut.")
            st.markdown("""
                1. Silakan unduh hasil komputasi dalam format **.csv** di atas.  
                2. Install **Google Earth Pro** pada device masing-masing.  
                3. Siapkan file **.kml** atau **.kmz** untuk risiko yang diinginkan. Jika ingin menggunakan layer banjir, Anda dapat mengakses melalui tautan berikut:
                
                👉 [Layer Banjir](https://bit.ly/LayerBanjir)
                
                Jika ingin semua layer, maka dapat mengakses tautan berikut:
                
                👉 [All Layer inaRISK](https://gis.bnpb.go.id/server/rest/services/inarisk)
                
                4. Buka file **.kml** atau **.kmz** secara langsung di Google Earth Pro, maka layer akan otomatis muncul di peta.  
                5. Buka file **.csv** hasil dari komputasi, lalu pilih delimiter yang sesuai (jika CSV biasa, pilih **comma** atau **koma (,)**).  
                6. Masukkan kolom **Longitude** dan **Latitude** sesuai dengan nama kolom pada data masing-masing.  
                7. Spesifikasikan tipe data pada setiap kolomnya. Biasanya Google akan otomatis mendeteksi, namun Anda tetap dapat mengeditnya jika ada kesalahan.
                8. Layer dan titik data sudah dapat diakses di Google Earth Pro.
            """)

            # Step 8: Peta Interaktif dengan Pydeck
            if lon_col and lat_col and not final.empty:
                st.subheader("🌐 Peta Sebaran Portfolio")

                # Mapping risiko ke bobot untuk heatmap
                risk_mapping = {
                    "Rendah": 0.3,
                    "Sedang": 0.6,
                    "Tinggi": 1.0,
                    "No Risk": 0.1
                }

                # Mapping warna untuk scatterplot
                color_mapping = {
                    "Rendah": [0, 255, 0, 180],     # Hijau transparan
                    "Sedang": [255, 255, 0, 180],   # Kuning transparan
                    "Tinggi": [255, 0, 0, 180],     # Merah transparan
                    "No Risk": [160, 160, 160, 180] # Abu-abu transparan
                }

                # Assign bobot dan warna
                if 'Kategori Risiko' in final.columns:
                    final["weight"] = final["Kategori Risiko"].map(risk_mapping).fillna(0.1)
                    final["color"] = final["Kategori Risiko"].map(color_mapping)
                    final["color"] = final["color"].apply(lambda x: x if isinstance(x, list) else [0, 0, 0, 180])
                else:
                    final["weight"] = 1
                    final["color"] = [[0, 0, 0, 180]] * len(final)

                # Buat popup info
                excluded_cols = ['SISTEM', 'NAMA FILE', 'Unique', 'TOC', 'gridcode', 'weight', 'color', 'Jumlah Lantai_Rev', 'Jumlah_Lantai_Fix']
                final["popup"] = final.apply(
                    lambda row: "<br>".join(
                        [
                            f"<b>{col}</b>: {row[col]}" if pd.notnull(row[col]) else f"<b>{col}</b>: -"
                            for col in final.columns if col not in excluded_cols
                        ]
                    ),
                    axis=1
                )

                # Data untuk map
                data = final[[lon_col, lat_col, "popup", "weight", "color"]].to_dict(orient="records")

                # Heatmap Layer
                heatmap_layer = pdk.Layer(
                    "HeatmapLayer",
                    data=data,
                    get_position=[lon_col, lat_col],
                    get_weight="weight",
                    aggregation="MEAN",
                    radiusPixels=25,
                )

                # Scatterplot Layer dengan warna berdasarkan risiko
                scatter_layer = pdk.Layer(
                    "ScatterplotLayer",
                    data=data,
                    get_position=[lon_col, lat_col],
                    get_fill_color="color",
                    get_radius=10,
                    pickable=True,
                    auto_highlight=True,
                )

                # View state untuk map
                view_state = pdk.ViewState(
                    latitude=float(final[lat_col].mean()),
                    longitude=float(final[lon_col].mean()),
                    zoom=5,
                    pitch=0,
                )

                # Combine semua layer ke dalam Deck
                deck = pdk.Deck(
                    layers=[heatmap_layer, scatter_layer],
                    initial_view_state=view_state,
                    tooltip={
                        "html": "{popup}",
                        "style": {
                            "backgroundColor": "white",
                            "color": "black",
                            "fontSize": "12px",
                            "lineHeight": "1",
                            "maxWidth": "200px",
                            "padding": "5px",
                        },
                    },
                    map_style="mapbox://styles/mapbox/dark-v10"
                )

                # Tampilkan map
                st.pydeck_chart(deck, use_container_width=True, height=750, width=1000)

            # Step 9: Ringkasan Hasil
            st.markdown("## 📊 Statistik Deskriptif")
            st.markdown(f"##### Jumlah Data: {len(final):,}")

            if 'Kategori Risiko' in final.columns:
                st.markdown("##### Distribusi Kategori Risiko")
                st.dataframe(
                    final['Kategori Risiko'].value_counts().rename_axis('Kategori').reset_index(name='Jumlah'),
                    use_container_width=True,
                    hide_index=True
                )

            if 'UY' in final.columns:
                st.markdown("### 📋 Ringkasan Berdasarkan Underwriting Year (UY)")
                summary_uy = final.groupby('UY').agg(
                    Jumlah_Polis=('UY', 'count'),
                    TotalTSI=(selected_tsi, 'sum'),
                    TotalPML=('PML', 'sum')
                ).reset_index().rename(columns={
                    'Jumlah_Polis': 'Jumlah Polis',
                    'TotalTSI': 'Sum TSI',
                    'TotalPML': 'Sum PML'
                })

                # Create a copy for display with formatted strings
                display_uy = summary_uy.copy()
                display_uy["Jumlah Polis"] = display_uy["Jumlah Polis"].apply(lambda x: f"{x:,.0f}".replace(",", "."))
                display_uy["Sum TSI"] = display_uy["Sum TSI"].apply(lambda x: f"{x:,.0f}".replace(",", "."))
                display_uy["Sum PML"] = display_uy["Sum PML"].apply(lambda x: f"{x:,.0f}".replace(",", "."))

                # Display the formatted dataframe
                st.dataframe(display_uy, use_container_width=True, hide_index=True)

                # Melt the original numerical dataframe for the chart
                summary_melted = summary_uy.melt(
                    id_vars='UY',
                    value_vars=['Sum TSI', 'Sum PML'],
                    var_name='Tipe',
                    value_name='Nilai'
                )

                chart = alt.Chart(summary_melted).mark_line(point=True).encode(
                    x=alt.X('UY:O', title='Underwriting Year', axis=alt.Axis(labelAngle=0)),
                    y=alt.Y('Nilai:Q', title='Nilai (Rp)', axis=alt.Axis(format='.1e')),
                    color=alt.Color(
                        'Tipe:N',
                        title='Jenis Nilai',
                        scale=alt.Scale(range=['#66a3ff', '#f08522'])
                    ),
                    tooltip=[
                        'UY',
                        'Tipe',
                        alt.Tooltip('Nilai:Q', title='Nilai (Rp)', format='.1e')
                    ]
                ).properties(
                    title='📈 Tren Sum TSI dan PML per UY',
                    width=700,
                    height=400
                ).interactive()

                st.altair_chart(chart, use_container_width=True)

            if 'Kategori Okupasi' in final.columns:
                st.markdown("### 📋 Ringkasan Berdasarkan Kategori Okupasi")
                summary_okupasi = final.groupby('Kategori Okupasi').agg(
                    jml_polis=('Kategori Okupasi', 'count'),
                    total_tsi=(selected_tsi, 'sum'),
                    total_pml=('PML', 'sum')
                ).reset_index().rename(columns={
                    'jml_polis': 'Jumlah Polis',
                    'total_tsi': 'Sum TSI',
                    'total_pml': 'Sum PML'
                })

                # Create a copy for display with formatted strings
                display_okupasi = summary_okupasi.copy()
                display_okupasi['Jumlah Polis'] = display_okupasi['Jumlah Polis'].apply(lambda x: f"{x:,.0f}".replace(",", "."))
                display_okupasi['Sum TSI'] = display_okupasi['Sum TSI'].apply(lambda x: f"{x:,.0f}".replace(",", "."))
                display_okupasi['Sum PML'] = display_okupasi['Sum PML'].apply(lambda x: f"{x:,.0f}".replace(",", "."))

                st.dataframe(display_okupasi, use_container_width=True, hide_index=True)

                # Melt the original numerical dataframe for the chart
                summary_melted = summary_okupasi.melt(
                    id_vars='Kategori Okupasi',
                    value_vars=['Sum TSI', 'Sum PML'],
                    var_name='Tipe',
                    value_name='Nilai'
                )

                chart = alt.Chart(summary_melted).mark_bar().encode(
                    x=alt.X('Kategori Okupasi:N', title='Kategori Okupasi', axis=alt.Axis(labelAngle=0)),
                    y=alt.Y(
                        'Nilai:Q',
                        title='Nilai (Rp)',
                        stack='zero',
                        axis=alt.Axis(format='.1e')
                    ),
                    color=alt.Color(
                        'Tipe:N',
                        title='Jenis Nilai',
                        scale=alt.Scale(range=['#66a3ff', '#f08522'])
                    ),
                    tooltip=[
                        'Kategori Okupasi',
                        'Tipe',
                        alt.Tooltip('Nilai:Q', title='Nilai (Rp)', format='.1e')
                    ]
                ).properties(
                    title='📊 Distribusi Sum TSI dan PML per Kategori Okupasi',
                    width=700,
                    height=400
                )

                st.altair_chart(chart, use_container_width=True)

            if 'Kategori Risiko' in final.columns:
                st.markdown("### 📋 Ringkasan Berdasarkan Kategori Risiko")
                summary_riskclass = final.groupby('Kategori Risiko').agg(
                    Jumlah_Polis=('Kategori Risiko', 'count'),
                    TotalTSI=(selected_tsi, 'sum'),
                    TotalPML=('PML', 'sum')
                ).reset_index().rename(columns={
                    'Jumlah_Polis': 'Jumlah Polis',
                    'TotalTSI': 'Sum TSI',
                    'TotalPML': 'Sum PML'
                })

                # Create a copy for display with formatted strings
                display_riskclass = summary_riskclass.copy()
                display_riskclass['Jumlah Polis'] = display_riskclass['Jumlah Polis'].apply(lambda x: f"{x:,.0f}".replace(",", "."))
                display_riskclass['Sum TSI'] = display_riskclass['Sum TSI'].apply(lambda x: f"{x:,.0f}".replace(",", "."))
                display_riskclass['Sum PML'] = display_riskclass['Sum PML'].apply(lambda x: f"{x:,.0f}".replace(",", "."))

                st.dataframe(display_riskclass, use_container_width=True, hide_index=True)

            if 'UY' in final.columns and 'Kategori Risiko' in final.columns:
                st.markdown("### 📋 Ringkasan Berdasarkan UY dan Kategori Risiko")
                count_polis = final.pivot_table(
                    index='UY',
                    columns=['Kategori Risiko'],
                    aggfunc='size'
                ).fillna(0).astype(int)

                sum_tsi = final.pivot_table(
                    index='UY',
                    columns=['Kategori Risiko'],
                    values=selected_tsi,
                    aggfunc='sum'
                ).fillna(0).astype(int)
                
                est_claim = final.pivot_table(
                    index='UY',
                    columns=['Kategori Risiko'],
                    values='PML',
                    aggfunc='sum'
                ).fillna(0).astype(int)

                def format_ribuan(df):
                    return df.apply(lambda x: x.map(lambda y: f"{int(y):,}".replace(",", ".") if pd.notnull(y) else y))

                st.markdown("##### Jumlah Polis")
                st.dataframe(format_ribuan(count_polis), use_container_width=True)

                st.markdown("##### Sum TSI")
                st.dataframe(format_ribuan(sum_tsi), use_container_width=True)
                
                st.markdown("##### Probable Maximum Loss")
                st.dataframe(format_ribuan(est_claim), use_container_width=True)

                st.markdown("### 📋 Ringkasan Berdasarkan UY, Kategori Risiko dan Okupasi")
                count_polis = final.pivot_table(
                    index='UY',
                    columns=['Kategori Okupasi', 'Kategori Risiko'],
                    aggfunc='size'
                ).fillna(0).astype(int)

                sum_tsi = final.pivot_table(
                    index='UY',
                    columns=['Kategori Okupasi', 'Kategori Risiko'],
                    values=selected_tsi,
                    aggfunc='sum'
                ).fillna(0).astype(int)

                est_claim = final.pivot_table(
                    index='UY',
                    columns=['Kategori Okupasi', 'Kategori Risiko'],
                    values='PML',
                    aggfunc='sum'
                ).fillna(0).astype(int)

                def format_ribuan(df):
                    return df.apply(lambda x: x.map(lambda y: f"{int(y):,}".replace(",", ".") if pd.notnull(y) else y))

                st.markdown("##### Jumlah Polis")
                st.dataframe(format_ribuan(count_polis), use_container_width=True)

                st.markdown("##### Sum TSI")
                st.dataframe(format_ribuan(sum_tsi), use_container_width=True)

                st.markdown("##### Probable Maximum Loss")
                st.dataframe(format_ribuan(est_claim), use_container_width=True)

                st.markdown("### 📋 Ringkasan Gabungan Berdasarkan UY dan Kode Okupasi")

                # Fungsi pivot formatter
                def get_pivot(df, value=None, label=''):
                    if value is None:
                        pivot = df.pivot_table(index='Kode Okupasi (2 digit awal)', columns='UY', aggfunc='size')
                    else:
                        pivot = df.pivot_table(index='Kode Okupasi (2 digit awal)', columns='UY', values=value, aggfunc='sum')
                    pivot = pivot.fillna(0).astype(int)
                    pivot['Jenis'] = label
                    return pivot.reset_index()

                # Buat masing-masing pivot
                count_df = get_pivot(final, None, 'Jumlah Polis')
                tsi_df = get_pivot(final, selected_tsi, 'Sum TSI')
                pml_df = get_pivot(final, 'PML', 'PML')

                # Gabungkan semua pivot
                combined = pd.concat([count_df, tsi_df, pml_df], ignore_index=True)

                # Jadikan 'Jenis' bertipe kategorikal dengan urutan yang diinginkan
                urutan_jenis = ['Jumlah Polis', 'Sum TSI', 'PML']
                combined['Jenis'] = pd.Categorical(combined['Jenis'], categories=urutan_jenis, ordered=True)

                # Urutkan berdasarkan Jenis lalu Kode Okupasi
                combined = combined.sort_values(by=['Jenis', 'Kode Okupasi (2 digit awal)'])

                # Pindahkan kolom 'Jenis' ke paling kiri
                cols = ['Jenis'] + [col for col in combined.columns if col != 'Jenis']
                combined = combined[cols]

                # Format angka dengan titik sebagai pemisah ribuan (Indonesia-style)
                uy_cols = combined.columns.difference(['Jenis', 'Kode Okupasi (2 digit awal)'])
                combined['Total'] = combined[uy_cols].apply(pd.to_numeric, errors='coerce').sum(axis=1)
                combined[uy_cols.tolist() + ['Total']] = combined[uy_cols.tolist() + ['Total']].applymap(
                    lambda x: f"{int(x):,}".replace(",", ".") if pd.notna(x) else x
                )

                # Tampilkan hasil di Streamlit
                st.dataframe(combined, use_container_width=True, hide_index=True)
        else:
            st.warning("⚠️ Tidak ada shapefile yang berhasil diproses.")
else:
    st.warning("⚠️ Silakan unggah file CSV terlebih dahulu.")
