import streamlit as st
import pandas as pd
import requests
import io
import os
from datetime import datetime, timedelta
import plotly.graph_objects as go
from plotly.subplots import make_subplots

# --- 設定 ---
st.set_page_config(page_title="JEPX & Imbalance Market Viewer", layout="wide")

# 【追加・修正】スマホ画面いっぱいに表示し、タッチ操作(ズーム)の干渉を防ぐCSS
st.markdown("""
    <style>
    /* Streamlitのデフォルトの余白を消して画面幅を最大限使う */
    .block-container {
        padding-top: 2rem !important;
        padding-bottom: 1rem !important;
        padding-left: 0.2rem !important;
        padding-right: 0.2rem !important;
        max-width: 100% !important;
    }
    /* 【追加】スマホのブラウザ標準のズームやスクロール干渉を防ぐ(Plotly専用) */
    .js-plotly-plot .plotly canvas {
        touch-action: none !important;
    }
    </style>
""", unsafe_allow_html=True)

AREAS = ["北海道", "東北", "東京", "中部", "北陸", "関西", "中国", "四国", "九州"]

# --- データ取得関数 ---
@st.cache_data(ttl=3600)
def get_imbalance_data(target_month):
    """APIからインバランス料金を取得し、整形する"""
    url = f"https://www.imbalanceprices-cs.jp/api/1.0/imb/price/{target_month}"
    headers = {"User-Agent": "Mozilla/5.0"}
    try:
        response = requests.get(url, headers=headers, timeout=20)
        if response.status_code == 200:
            csv_text = response.content.decode('cp932')
            lines = csv_text.split('\n')
            header_idx = next(i for i, line in enumerate(lines) if line.count(',') > 10)
            
            df = pd.read_csv(io.StringIO(csv_text), skiprows=header_idx + 1)
            
            if "Unnamed: 22" in df.columns:
                df = df.loc[:, :"Unnamed: 22"]
            
            rename_dict = {}
            has_date, has_time = False, False
            for col in df.columns:
                if not has_date and any(kw in col for kw in ['受渡日', '対象日', '日付', '年月日']):
                    rename_dict[col] = 'Date'
                    has_date = True
                if not has_time and any(kw in col for kw in ['時刻', 'コマ']):
                    rename_dict[col] = 'Time'
                    has_time = True
            
            df.rename(columns=rename_dict, inplace=True)
            df.columns = [col.replace('エリア', '') for col in df.columns]
            
            if 'Date' in df.columns and 'Time' in df.columns:
                df['Date'] = pd.to_numeric(df['Date'], errors='coerce')
                df['Time'] = pd.to_numeric(df['Time'], errors='coerce')
                df = df.dropna(subset=['Date', 'Time'])
                
                df['Date'] = df['Date'].astype(int).astype(str)
                df['Time'] = df['Time'].astype(int)
                
                base_date = pd.to_datetime(df['Date'], format='%Y%m%d')
                time_delta = pd.to_timedelta((df['Time'] - 1) * 30, unit='m')
                df['Datetime'] = base_date + time_delta
                df = df.set_index('Datetime')
                
                return df[AREAS].apply(pd.to_numeric, errors='coerce')
        return None
    except Exception as e:
        st.error(f"インバランスデータ取得エラー ({target_month}): {e}")
        return None

def process_spot_df(df):
    """取得したスポットデータの整形処理を行う共通関数"""
    df['Date'] = pd.to_datetime(df['受渡日'])
    time_delta = pd.to_timedelta((df['時刻コード'] - 1) * 30, unit='m')
    df['Datetime'] = df['Date'] + time_delta
    df = df.set_index('Datetime')
    
    rename_dict = {
        f'エリアプライス{area}(円/kWh)': f'{area}_スポット' for area in AREAS
    }
    rename_dict.update({
        'システムプライス(円/kWh)': 'システム_スポット'
    })
    
    df = df.rename(columns=rename_dict)
    spot_cols = [c for c in df.columns if 'スポット' in c]
    df = df[spot_cols]
    df = df[~df.index.duplicated(keep='first')].sort_index()
    
    return df.apply(pd.to_numeric, errors='coerce')

@st.cache_data(ttl=3600)
def get_spot_data(target_year, uploaded_bytes=None):
    """JEPX公式サイトから直接CSVを取得するか、アップロードされたデータを利用する"""
    if uploaded_bytes is not None:
        try:
            df = pd.read_csv(io.BytesIO(uploaded_bytes), encoding='cp932')
            return process_spot_df(df)
        except Exception as e:
            st.error(f"アップロードされたファイルの読み込みに失敗しました: {e}")
            return None

    urls = [
        f"https://www.jepx.jp/market/excel/spot_summary_{target_year}.csv",
        f"https://www.jepx.org/market/excel/spot_summary_{target_year}.csv",
    ]
    
    for url in urls:
        try:
            res = requests.get(url, timeout=10)
            if res.status_code == 200:
                csv_text = res.content.decode('cp932')
                df = pd.read_csv(io.StringIO(csv_text))
                return process_spot_df(df)
        except Exception:
            continue

    current_dir = os.path.dirname(__file__)
    file_path = os.path.join(current_dir, f"spot_summary_{target_year}.csv")
    if os.path.exists(file_path):
        try:
            df = pd.read_csv(file_path, encoding='cp932')
            return process_spot_df(df)
        except Exception:
            pass

    return None

# --- UI構築 ---
st.title("📈 Market View: JEPX Spot vs Imbalance")
st.write("💡 **Tips**: チャート上でドラッグすると左右に移動(パン)できます。二本指でスクロール（ピンチアウト）すると、過去・未来のデータまでシームレスにズームアウトして確認できます。")

st.sidebar.header("チャート設定")
selected_area = st.sidebar.selectbox("表示エリア", AREAS, index=2)
start_date = st.sidebar.date_input("初期表示 開始日", value=datetime.today().date() - timedelta(days=7))
end_date = st.sidebar.date_input("初期表示 終了日", value=datetime.today().date())

st.sidebar.markdown("---")
st.sidebar.subheader("データ取得設定")
st.sidebar.caption("※通常はJEPX公式サイトから自動取得するため操作不要です。自動取得に失敗する場合のみ、スマホやPCからCSVをアップロードしてください。")
uploaded_file = st.sidebar.file_uploader("手動アップロード (任意)")

if start_date <= end_date:
    with st.spinner("市場データを取得中...（ズームアウト用のデータも含めて読み込んでいます）"):
        start_dt = datetime.combine(start_date, datetime.min.time())
        end_dt = datetime.combine(end_date, datetime.max.time())
        
        # 【変更】左側（過去）は90日前から。右側（未来）は、本日+7日または終了日のどちらか遅い方まで読み込む
        fetch_start_dt = start_dt - timedelta(days=90)
        latest_possible_dt = datetime.combine(datetime.today().date() + timedelta(days=7), datetime.max.time())
        fetch_end_dt = max(end_dt, latest_possible_dt)
        
        months_to_fetch = pd.date_range(start=fetch_start_dt.replace(day=1), end=fetch_end_dt, freq='MS').strftime("%Y%m").tolist()
        if fetch_start_dt.strftime("%Y%m") not in months_to_fetch:
            months_to_fetch.insert(0, fetch_start_dt.strftime("%Y%m"))
        months_to_fetch = sorted(list(set(months_to_fetch)))

        imb_list = [get_imbalance_data(m) for m in months_to_fetch]
        valid_imb_list = [df for df in imb_list if df is not None]
        imb_df = pd.concat(valid_imb_list) if valid_imb_list else pd.DataFrame()
        if not imb_df.empty:
            imb_df = imb_df[~imb_df.index.duplicated(keep='first')].sort_index()

        target_year = start_dt.year
        uploaded_bytes = uploaded_file.getvalue() if uploaded_file is not None else None
        spot_df = get_spot_data(target_year, uploaded_bytes)

        if not imb_df.empty and spot_df is not None:
            merged_df = pd.concat([imb_df[[selected_area]], spot_df[[f"{selected_area}_スポット"]]], axis=1)
            merged_df.columns = ["Imbalance", "Spot"]
            
            # 【変更】取得した全範囲（過去90日〜現在・未来）のデータを保持しておく
            merged_df = merged_df[(merged_df.index >= fetch_start_dt) & (merged_df.index <= fetch_end_dt)].dropna()

            if not merged_df.empty:
                merged_df['Spread'] = merged_df['Imbalance'] - merged_df['Spot']
                merged_df['Spread_Color'] = merged_df['Spread'].apply(lambda x: '#ff4d4d' if x >= 0 else '#00cc96')

                fig = make_subplots(
                    rows=2, cols=1, 
                    shared_xaxes=True, 
                    row_heights=[0.7, 0.3],
                    vertical_spacing=0.08,
                    subplot_titles=(f"{selected_area}エリア 価格推移 (円/kWh)", "インバランス・スプレッド (Imbalance - Spot)")
                )

                fig.add_trace(go.Scatter(
                    x=merged_df.index, y=merged_df['Spot'],
                    name='Spot Price', mode='lines',
                    line=dict(color='#00cc96', width=2, shape='vh')
                ), row=1, col=1)

                fig.add_trace(go.Scatter(
                    x=merged_df.index, y=merged_df['Imbalance'],
                    name='Imbalance Price', mode='lines',
                    line=dict(color='#ff9900', width=2, shape='vh')
                ), row=1, col=1)

                fig.add_trace(go.Bar(
                    x=merged_df.index, y=merged_df['Spread'],
                    name='Spread',
                    marker_color=merged_df['Spread_Color'],
                    opacity=0.8
                ), row=2, col=1)

                fig.update_layout(
                    template="plotly_dark",
                    height=650,
                    margin=dict(l=5, r=5, t=90, b=20),
                    hovermode="x unified",
                    showlegend=True,
                    legend=dict(
                        orientation="h", 
                        yanchor="bottom", 
                        y=1.05,
                        xanchor="center", 
                        x=0.5
                    ),
                    dragmode='pan' 
                )

                # 初期表示のズーム位置だけを [start_dt, end_dt] に設定（範囲外のデータも裏には存在している）
                fig.update_xaxes(
                    range=[start_dt, end_dt],
                    rangeslider=dict(visible=False),
                    showgrid=True, gridcolor='#333333',
                    fixedrange=False, # 【追加】ズームを明示的に許可
                    row=1, col=1
                )
                fig.update_xaxes(
                    range=[start_dt, end_dt],
                    rangeslider=dict(visible=True, thickness=0.08),
                    showgrid=True, gridcolor='#333333',
                    fixedrange=False, # 【追加】ズームを明示的に許可
                    row=2, col=1
                )

                fig.update_yaxes(showgrid=True, gridcolor='#333333', fixedrange=False, row=1, col=1)
                fig.update_yaxes(showgrid=True, gridcolor='#333333', fixedrange=False, row=2, col=1)

                # 【追加】タッチ操作をよりスムーズにするためのコンフィグ調整
                chart_config = {
                    "scrollZoom": True, 
                    "displayModeBar": False,
                    "responsive": True,
                    "doubleClick": "reset"
                }
                st.plotly_chart(fig, use_container_width=True, config=chart_config)
                
                with st.expander("📊 データの詳細を表示 (読み込み済みの全期間)"):
                    st.dataframe(merged_df.sort_index(ascending=False).drop(columns=['Spread_Color']).style.format("{:.2f}"))

            else:
                st.warning("選択された期間のデータが存在しません。")
        else:
            if spot_df is None:
                st.error("データの読み込みに失敗しました。自動取得がブロックされている可能性があります。サイドバーから最新のCSVファイルをアップロードしてください。")
            else:
                st.error("インバランスデータの読み込みに失敗しました。")
else:
    st.error("終了日は開始日以降の日付を選択してください。")
