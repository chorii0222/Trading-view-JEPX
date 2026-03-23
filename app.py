import streamlit as st
import pandas as pd
import numpy as np
import requests
import io
import os
from datetime import datetime, timedelta
import plotly.graph_objects as go
from plotly.subplots import make_subplots

# --- 設定 ---
st.set_page_config(page_title="JEPX & Imbalance Market Viewer", layout="wide")

# スマホ画面いっぱいに表示するためのCSS
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

def calculate_rsi(series, period=14):
    """RSIを計算する関数"""
    delta = series.diff()
    up = delta.clip(lower=0)
    down = -1 * delta.clip(upper=0)
    ema_up = up.ewm(com=period-1, adjust=False).mean()
    ema_down = down.ewm(com=period-1, adjust=False).mean()
    rs = ema_up / ema_down
    return 100 - (100 / (1 + rs))

# --- UI構築 ---
st.title("📈 Market View: JEPX Spot vs Imbalance")
st.write("💡 **Tips**: チャート上でドラッグすると左右に移動(パン)できます。二本指でスクロール（ピンチアウト）すると、過去のデータ（最大90日前）までシームレスにズームアウトして確認できます。")

st.sidebar.header("チャート設定")
selected_area = st.sidebar.selectbox("表示エリア", AREAS, index=2)
start_date = st.sidebar.date_input("初期表示 開始日", value=datetime.today().date() - timedelta(days=7))
end_date = st.sidebar.date_input("初期表示 終了日", value=datetime.today().date())

st.sidebar.markdown("---")
st.sidebar.subheader("👁️ 表示データの選択")
show_spot = st.sidebar.checkbox("スポット価格を表示", value=True)
show_imb = st.sidebar.checkbox("インバランス料金を表示", value=True)

st.sidebar.markdown("---")
st.sidebar.subheader("📈 テクニカル指標")

selected_periods = st.sidebar.multiselect(
    "SMA/EMA/BBの期間 (時間) ※複数選択可",
    options=[3, 6, 12, 24, 48, 72, 168],
    default=[24]
)

st.sidebar.write("【SMA (単純移動平均線)】")
show_sma_spot = st.sidebar.checkbox("スポット価格のSMA", value=False)
show_sma_imb = st.sidebar.checkbox("インバランス料金のSMA", value=False)

st.sidebar.write("【EMA (指数平滑移動平均線)】")
show_ema_spot = st.sidebar.checkbox("スポット価格のEMA", value=False)
show_ema_imb = st.sidebar.checkbox("インバランス料金のEMA", value=False)

st.sidebar.write("【ボリンジャーバンド (2σ)】")
show_bb_spot = st.sidebar.checkbox("スポット価格のBB", value=False)
show_bb_imb = st.sidebar.checkbox("インバランス料金のBB", value=False)

st.sidebar.write("【一目均衡表 (9, 26, 52)】")
show_ichimoku_spot = st.sidebar.checkbox("スポット価格の一目均衡表", value=False)
show_ichimoku_imb = st.sidebar.checkbox("インバランス料金の一目均衡表", value=False)

st.sidebar.write("【MACD (12, 26, 9)】")
show_macd_spot = st.sidebar.checkbox("スポット価格のMACD", value=False)
show_macd_imb = st.sidebar.checkbox("インバランス料金のMACD", value=False)

st.sidebar.write("【RSI (14)】")
show_rsi_spot = st.sidebar.checkbox("スポット価格のRSI", value=False)
show_rsi_imb = st.sidebar.checkbox("インバランス料金のRSI", value=False)

st.sidebar.markdown("---")
st.sidebar.subheader("データ取得設定")
st.sidebar.caption("※通常はJEPX公式サイトから自動取得するため操作不要です。自動取得に失敗する場合のみ、スマホやPCからCSVをアップロードしてください。")
uploaded_file = st.sidebar.file_uploader("手動アップロード (任意)")

if start_date <= end_date:
    with st.spinner("市場データを取得中...（ズームアウト用の過去データも含めて読み込んでいます）"):
        start_dt = datetime.combine(start_date, datetime.min.time())
        end_dt = datetime.combine(end_date, datetime.max.time())
        
        fetch_start_dt = start_dt - timedelta(days=90)
        
        months_to_fetch = pd.date_range(start=fetch_start_dt.replace(day=1), end=end_dt, freq='MS').strftime("%Y%m").tolist()
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
            
            merged_df = merged_df[(merged_df.index >= fetch_start_dt) & (merged_df.index <= end_dt)].dropna()

            if not merged_df.empty:
                merged_df['Spread'] = merged_df['Imbalance'] - merged_df['Spot']
                merged_df['Spread_Color'] = merged_df['Spread'].apply(lambda x: '#ff4d4d' if x >= 0 else '#00cc96')

                # インジケーターの計算
                for h in selected_periods:
                    ma_window_periods = h * 2 
                    
                    if show_sma_spot or show_bb_spot:
                        merged_df[f'Spot_SMA_{h}'] = merged_df['Spot'].rolling(window=ma_window_periods, min_periods=1).mean()
                    if show_ema_spot:
                        merged_df[f'Spot_EMA_{h}'] = merged_df['Spot'].ewm(span=ma_window_periods, adjust=False).mean()
                    if show_bb_spot:
                        merged_df[f'Spot_STD_{h}'] = merged_df['Spot'].rolling(window=ma_window_periods, min_periods=1).std()
                        merged_df[f'Spot_BB_Upper_{h}'] = merged_df[f'Spot_SMA_{h}'] + (merged_df[f'Spot_STD_{h}'] * 2)
                        merged_df[f'Spot_BB_Lower_{h}'] = merged_df[f'Spot_SMA_{h}'] - (merged_df[f'Spot_STD_{h}'] * 2)

                    if show_sma_imb or show_bb_imb:
                        merged_df[f'Imb_SMA_{h}'] = merged_df['Imbalance'].rolling(window=ma_window_periods, min_periods=1).mean()
                    if show_ema_imb:
                        merged_df[f'Imb_EMA_{h}'] = merged_df['Imbalance'].ewm(span=ma_window_periods, adjust=False).mean()
                    if show_bb_imb:
                        merged_df[f'Imb_STD_{h}'] = merged_df['Imbalance'].rolling(window=ma_window_periods, min_periods=1).std()
                        merged_df[f'Imb_BB_Upper_{h}'] = merged_df[f'Imb_SMA_{h}'] + (merged_df[f'Imb_STD_{h}'] * 2)
                        merged_df[f'Imb_BB_Lower_{h}'] = merged_df[f'Imb_SMA_{h}'] - (merged_df[f'Imb_STD_{h}'] * 2)

                if show_ichimoku_spot:
                    merged_df['Spot_Tenkan'] = (merged_df['Spot'].rolling(window=9, min_periods=1).max() + merged_df['Spot'].rolling(window=9, min_periods=1).min()) / 2
                    merged_df['Spot_Kijun'] = (merged_df['Spot'].rolling(window=26, min_periods=1).max() + merged_df['Spot'].rolling(window=26, min_periods=1).min()) / 2
                    merged_df['Spot_Senkou_A'] = (merged_df['Spot_Tenkan'] + merged_df['Spot_Kijun']) / 2
                    merged_df['Spot_Senkou_B'] = (merged_df['Spot'].rolling(window=52, min_periods=1).max() + merged_df['Spot'].rolling(window=52, min_periods=1).min()) / 2

                if show_ichimoku_imb:
                    merged_df['Imb_Tenkan'] = (merged_df['Imbalance'].rolling(window=9, min_periods=1).max() + merged_df['Imbalance'].rolling(window=9, min_periods=1).min()) / 2
                    merged_df['Imb_Kijun'] = (merged_df['Imbalance'].rolling(window=26, min_periods=1).max() + merged_df['Imbalance'].rolling(window=26, min_periods=1).min()) / 2
                    merged_df['Imb_Senkou_A'] = (merged_df['Imb_Tenkan'] + merged_df['Imb_Kijun']) / 2
                    merged_df['Imb_Senkou_B'] = (merged_df['Imbalance'].rolling(window=52, min_periods=1).max() + merged_df['Imbalance'].rolling(window=52, min_periods=1).min()) / 2

                # MACDの計算
                if show_macd_spot:
                    merged_df['Spot_MACD_12'] = merged_df['Spot'].ewm(span=12, adjust=False).mean()
                    merged_df['Spot_MACD_26'] = merged_df['Spot'].ewm(span=26, adjust=False).mean()
                    merged_df['Spot_MACD'] = merged_df['Spot_MACD_12'] - merged_df['Spot_MACD_26']
                    merged_df['Spot_MACD_Signal'] = merged_df['Spot_MACD'].ewm(span=9, adjust=False).mean()
                    merged_df['Spot_MACD_Hist'] = merged_df['Spot_MACD'] - merged_df['Spot_MACD_Signal']
                
                if show_macd_imb:
                    merged_df['Imb_MACD_12'] = merged_df['Imbalance'].ewm(span=12, adjust=False).mean()
                    merged_df['Imb_MACD_26'] = merged_df['Imbalance'].ewm(span=26, adjust=False).mean()
                    merged_df['Imb_MACD'] = merged_df['Imb_MACD_12'] - merged_df['Imb_MACD_26']
                    merged_df['Imb_MACD_Signal'] = merged_df['Imb_MACD'].ewm(span=9, adjust=False).mean()
                    merged_df['Imb_MACD_Hist'] = merged_df['Imb_MACD'] - merged_df['Imb_MACD_Signal']

                # RSIの計算
                if show_rsi_spot:
                    merged_df['Spot_RSI'] = calculate_rsi(merged_df['Spot'], 14)
                if show_rsi_imb:
                    merged_df['Imb_RSI'] = calculate_rsi(merged_df['Imbalance'], 14)

                # --- サブプロットの動的レイアウト構成 ---
                show_macd = show_macd_spot or show_macd_imb
                show_rsi = show_rsi_spot or show_rsi_imb
                
                rows = 2
                row_heights = [0.6, 0.2] # メインチャートとスプレッド
                subplot_titles = [f"{selected_area}エリア 価格推移 (円/kWh)", "インバランス・スプレッド (Imbalance - Spot)"]
                
                if show_macd:
                    rows += 1
                    row_heights.append(0.2)
                    subplot_titles.append("MACD (12, 26, 9)")
                if show_rsi:
                    rows += 1
                    row_heights.append(0.2)
                    subplot_titles.append("RSI (14)")

                row_spread = 2
                row_macd = 3 if show_macd else None
                row_rsi = (3 if not show_macd else 4) if show_rsi else None
                
                # サブプロットの数に応じて全体の高さを調整
                total_height = 650 + ((rows - 2) * 200)

                fig = make_subplots(
                    rows=rows, cols=1, 
                    shared_xaxes=True, 
                    row_heights=row_heights,
                    vertical_spacing=0.06,
                    subplot_titles=subplot_titles
                )

                dash_styles = ['dot', 'dash', 'dashdot', 'longdash']

                # ーーー メインチャートへの指標描画 ーーー
                for i, h in enumerate(selected_periods):
                    d_style = dash_styles[i % len(dash_styles)]

                    if show_bb_spot:
                        fig.add_trace(go.Scatter(x=merged_df.index, y=merged_df[f'Spot_BB_Upper_{h}'], mode='lines', line=dict(color='rgba(0, 204, 150, 0.4)', width=1, dash=d_style), showlegend=False), row=1, col=1)
                        fig.add_trace(go.Scatter(x=merged_df.index, y=merged_df[f'Spot_BB_Lower_{h}'], name=f'Spot BB {h}h', mode='lines', fill='tonexty', fillcolor='rgba(0, 204, 150, 0.05)', line=dict(color='rgba(0, 204, 150, 0.4)', width=1, dash=d_style), showlegend=False), row=1, col=1)
                    if show_sma_spot:
                        fig.add_trace(go.Scatter(x=merged_df.index, y=merged_df[f'Spot_SMA_{h}'], name=f'Spot SMA {h}h', mode='lines', line=dict(color='#00cc96', width=1.5, dash=d_style)), row=1, col=1)
                    if show_ema_spot:
                        fig.add_trace(go.Scatter(x=merged_df.index, y=merged_df[f'Spot_EMA_{h}'], name=f'Spot EMA {h}h', mode='lines', line=dict(color='#66ffcc', width=1.5, dash=d_style)), row=1, col=1)

                    if show_bb_imb:
                        fig.add_trace(go.Scatter(x=merged_df.index, y=merged_df[f'Imb_BB_Upper_{h}'], mode='lines', line=dict(color='rgba(255, 153, 0, 0.4)', width=1, dash=d_style), showlegend=False), row=1, col=1)
                        fig.add_trace(go.Scatter(x=merged_df.index, y=merged_df[f'Imb_BB_Lower_{h}'], name=f'Imb BB {h}h', mode='lines', fill='tonexty', fillcolor='rgba(255, 153, 0, 0.05)', line=dict(color='rgba(255, 153, 0, 0.4)', width=1, dash=d_style), showlegend=False), row=1, col=1)
                    if show_sma_imb:
                        fig.add_trace(go.Scatter(x=merged_df.index, y=merged_df[f'Imb_SMA_{h}'], name=f'Imb SMA {h}h', mode='lines', line=dict(color='#ff9900', width=1.5, dash=d_style)), row=1, col=1)
                    if show_ema_imb:
                        fig.add_trace(go.Scatter(x=merged_df.index, y=merged_df[f'Imb_EMA_{h}'], name=f'Imb EMA {h}h', mode='lines', line=dict(color='#ffcc66', width=1.5, dash=d_style)), row=1, col=1)

                future_idx = merged_df.index + pd.Timedelta(minutes=30*26)

                if show_ichimoku_spot:
                    fig.add_trace(go.Scatter(x=future_idx, y=merged_df['Spot_Senkou_A'], mode='lines', line=dict(color='rgba(0, 204, 150, 0)', width=0), showlegend=False, hoverinfo='skip'), row=1, col=1)
                    fig.add_trace(go.Scatter(x=future_idx, y=merged_df['Spot_Senkou_B'], name='Spot 雲', mode='lines', fill='tonexty', fillcolor='rgba(0, 204, 150, 0.15)', line=dict(color='rgba(0, 204, 150, 0)', width=0)), row=1, col=1)
                    fig.add_trace(go.Scatter(x=merged_df.index, y=merged_df['Spot_Tenkan'], name='Spot 転換線', mode='lines', line=dict(color='#00cc96', width=1)), row=1, col=1)
                    fig.add_trace(go.Scatter(x=merged_df.index, y=merged_df['Spot_Kijun'], name='Spot 基準線', mode='lines', line=dict(color='#00cc96', width=1, dash='dot')), row=1, col=1)

                if show_ichimoku_imb:
                    fig.add_trace(go.Scatter(x=future_idx, y=merged_df['Imb_Senkou_A'], mode='lines', line=dict(color='rgba(255, 153, 0, 0)', width=0), showlegend=False, hoverinfo='skip'), row=1, col=1)
                    fig.add_trace(go.Scatter(x=future_idx, y=merged_df['Imb_Senkou_B'], name='Imb 雲', mode='lines', fill='tonexty', fillcolor='rgba(255, 153, 0, 0.15)', line=dict(color='rgba(255, 153, 0, 0)', width=0)), row=1, col=1)
                    fig.add_trace(go.Scatter(x=merged_df.index, y=merged_df['Imb_Tenkan'], name='Imb 転換線', mode='lines', line=dict(color='#ff9900', width=1)), row=1, col=1)
                    fig.add_trace(go.Scatter(x=merged_df.index, y=merged_df['Imb_Kijun'], name='Imb 基準線', mode='lines', line=dict(color='#ff9900', width=1, dash='dot')), row=1, col=1)

                if show_spot:
                    fig.add_trace(go.Scatter(x=merged_df.index, y=merged_df['Spot'], name='Spot Price', mode='lines', line=dict(color='#00cc96', width=2, shape='vh')), row=1, col=1)
                if show_imb:
                    fig.add_trace(go.Scatter(x=merged_df.index, y=merged_df['Imbalance'], name='Imbalance Price', mode='lines', line=dict(color='#ff9900', width=2, shape='vh')), row=1, col=1)

                # ーーー スプレッド描画 (Row=2) ーーー
                fig.add_trace(go.Bar(x=merged_df.index, y=merged_df['Spread'], name='Spread', marker_color=merged_df['Spread_Color'], opacity=0.8), row=row_spread, col=1)

                # ーーー MACD描画 (Row=3) ーーー
                if show_macd:
                    if show_macd_spot:
                        spot_macd_colors = ['rgba(0, 204, 150, 0.7)' if val >= 0 else 'rgba(255, 77, 77, 0.7)' for val in merged_df['Spot_MACD_Hist']]
                        fig.add_trace(go.Bar(x=merged_df.index, y=merged_df['Spot_MACD_Hist'], name='Spot MACD Hist', marker_color=spot_macd_colors), row=row_macd, col=1)
                        fig.add_trace(go.Scatter(x=merged_df.index, y=merged_df['Spot_MACD'], name='Spot MACD', mode='lines', line=dict(color='#00cc96', width=1.5)), row=row_macd, col=1)
                        fig.add_trace(go.Scatter(x=merged_df.index, y=merged_df['Spot_MACD_Signal'], name='Spot Signal', mode='lines', line=dict(color='#66ffcc', width=1, dash='dot')), row=row_macd, col=1)
                    if show_macd_imb:
                        imb_macd_colors = ['rgba(255, 153, 0, 0.7)' if val >= 0 else 'rgba(255, 77, 77, 0.7)' for val in merged_df['Imb_MACD_Hist']]
                        fig.add_trace(go.Bar(x=merged_df.index, y=merged_df['Imb_MACD_Hist'], name='Imb MACD Hist', marker_color=imb_macd_colors), row=row_macd, col=1)
                        fig.add_trace(go.Scatter(x=merged_df.index, y=merged_df['Imb_MACD'], name='Imb MACD', mode='lines', line=dict(color='#ff9900', width=1.5)), row=row_macd, col=1)
                        fig.add_trace(go.Scatter(x=merged_df.index, y=merged_df['Imb_MACD_Signal'], name='Imb Signal', mode='lines', line=dict(color='#ffcc66', width=1, dash='dot')), row=row_macd, col=1)

                # ーーー RSI描画 (Row=4) ーーー
                if show_rsi:
                    if show_rsi_spot:
                        fig.add_trace(go.Scatter(x=merged_df.index, y=merged_df['Spot_RSI'], name='Spot RSI', mode='lines', line=dict(color='#00cc96', width=1.5)), row=row_rsi, col=1)
                    if show_rsi_imb:
                        fig.add_trace(go.Scatter(x=merged_df.index, y=merged_df['Imb_RSI'], name='Imb RSI', mode='lines', line=dict(color='#ff9900', width=1.5)), row=row_rsi, col=1)
                    
                    # RSIの基準線 (70と30)
                    fig.add_hline(y=70, line_dash="dash", line_color="gray", line_width=1, row=row_rsi, col=1)
                    fig.add_hline(y=30, line_dash="dash", line_color="gray", line_width=1, row=row_rsi, col=1)
                    fig.update_yaxes(range=[0, 100], row=row_rsi, col=1)

                fig.update_layout(
                    template="plotly_dark",
                    height=total_height,
                    margin=dict(l=5, r=5, t=90, b=20),
                    hovermode="x unified",
                    showlegend=True,
                    legend=dict(orientation="h", yanchor="bottom", y=1.05, xanchor="center", x=0.5),
                    dragmode='pan' 
                )

                # すべてのX軸設定ループ
                for r in range(1, rows + 1):
                    fig.update_xaxes(
                        range=[start_dt, end_dt],
                        rangeslider=dict(visible=(r == rows), thickness=0.08), # 一番下のチャートにだけスライダーを表示
                        showgrid=True, gridcolor='#333333',
                        row=r, col=1
                    )

                # Y軸設定 (価格とスプレッドは0以上固定)
                fig.update_yaxes(rangemode="nonnegative", showgrid=True, gridcolor='#333333', row=1, col=1)
                fig.update_yaxes(rangemode="nonnegative", showgrid=True, gridcolor='#333333', row=2, col=1)
                if show_macd:
                    fig.update_yaxes(showgrid=True, gridcolor='#333333', row=row_macd, col=1)
                if show_rsi:
                    fig.update_yaxes(showgrid=True, gridcolor='#333333', row=row_rsi, col=1)

                st.plotly_chart(fig, use_container_width=True, config={"scrollZoom": True, "displayModeBar": False})
                
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
