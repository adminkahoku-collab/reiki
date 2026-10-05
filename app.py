import io
import os
import re
import time
import zipfile
from bs4 import BeautifulSoup
from google import genai
import streamlit as st

# --- ページ設定 ---
st.set_page_config(page_title="例規ナレッジ化処理ツール", layout="wide")

# --- 1. パスワード認証機能 ---
PASSWORD = "reiki063215"

if "authenticated" not in st.session_state:
    st.session_state.authenticated = False

if not st.session_state.authenticated:
    st.title("🔒 例規データ変換システム")
    input_pwd = st.text_input("パスワードを入力してください", type="password")
    if st.button("ログイン"):
        if input_pwd == PASSWORD:
            st.session_state.authenticated = True
            st.rerun()
        else:
            st.error("パスワードが正しくありません。")
    st.stop()


# --- 2. API Key の取得（st.secrets より取得） ---
api_key = None
if "GEMINI_API_KEY" in st.secrets:
    api_key = st.secrets["GEMINI_API_KEY"]


# --- 3. HTML ➔ シンプル Markdown 変換関数 ---
def convert_html_to_markdown(html_content: str) -> str:
    """例規HTMLからタイトルと本文を抽出し、##見出しの標準Markdownへ変換"""
    soup = BeautifulSoup(html_content, "html.parser")

    # タグや余分な装飾の除去・抽出処理（例規用）
    # タイトル取得（h1, h2, title等から抽出）
    title_tag = soup.find(["h1", "h2", "title"])
    title = title_tag.get_text(strip=True) if title_tag else "無題の例規"

    # 本文テキスト取得
    for element in soup(["script", "style", "meta", "link"]):
        element.decompose()

    text = soup.get_text(separator="\n")
    # 余分な空行を整理
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    body_text = "\n".join(lines)

    # Markdown構築
    md_content = f"## {title}\n\n{body_text}\n"
    return md_content


# --- 4. メイン画面 ---
st.title("📄 例規データ ナレッジ化処理システム")
st.caption(
    "第1段階: DVD Zip(HTML) ➔ 標準MD化 & ボリューム計測 ➔ 第2段階: Gemini要約付与"
)

# セッション状態の初期化
if "stage1_files" not in st.session_state:
    st.session_state.stage1_files = {}


# ==============================================================================
# 【第1段階】DVD Zip (HTML等) ➔ 標準 Markdown 変換 & ダウンロード & ボリューム計測
# ==============================================================================
st.header("【第1段階】DVD Zipファイルの読み込み・Markdown変換・ボリューム確認")

uploaded_zip = st.file_uploader(
    "DVDデータがまとまった Zip ファイルを選択してください",
    type=["zip"],
)

if uploaded_zip:
    if st.button("⚙️ 第1段階: Markdown変換を実行"):
        stage1_output = {}
        total_rules_count = 0

        with zipfile.ZipFile(uploaded_zip, "r") as z:
            for zip_info in z.infolist():
                # HTMLファイルを対象（隠しファイルやフォルダ等を除外）
                if zip_info.filename.endswith(
                    (".html", ".htm")
                ) and not zip_info.filename.startswith("__MACOSX"):
                    with z.open(zip_info) as f:
                        # 文字コード対応（cp932/shift_jis/utf-8）
                        content_bytes = f.read()
                        try:
                            html_text = content_bytes.decode("cp932")
                        except UnicodeDecodeError:
                            html_text = content_bytes.decode(
                                "utf-8", errors="ignore"
                            )

                        # Markdownへ変換
                        md_text = convert_html_to_markdown(html_text)
                        # 出力ファイル名 (.html -> .md)
                        md_filename = (
                            os.path.basename(zip_info.filename).rsplit(".", 1)[
                                0
                            ]
                            + ".md"
                        )

                        # '## ' 見出し数（条例数）のカウント
                        rules = re.findall(
                            r"^##\s+(.+)$", md_text, re.MULTILINE
                        )
                        rule_count = len(rules)

                        stage1_output[md_filename] = {
                            "content": md_text,
                            "rule_count": rule_count,
                        }
                        total_rules_count += rule_count

        st.session_state.stage1_files = stage1_output
        st.success("✅ 第1段階のMarkdown変換処理が完了しました！")

# 第1段階のデータが存在する場合のボリューム表示 & ダウンロードボタン
if st.session_state.stage1_files:
    file_count = len(st.session_state.stage1_files)
    total_rules = sum(
        v["rule_count"] for v in st.session_state.stage1_files.values()
    )

    col1, col2, col3 = st.columns(3)
    col1.metric("総ファイル数", f"{file_count} 件")
    col2.metric("総条例（##）数", f"{total_rules} 件")

    # 無償API (1件約4.5秒待機) の場合の所要時間計算
    estimated_seconds = total_rules * 4.5
    est_min = int(estimated_seconds // 60)
    est_sec = int(estimated_seconds % 60)
    col3.metric("第2段階の予想処理時間", f"約 {est_min}分 {est_sec}秒")

    st.info(
        f"📊 ボリューム計測結果: **全 {total_rules} 件** の条文（`##`）が見つかりました。"
    )

    # 第1段階のMarkdown Zip作成 & ダウンロード
    zip_buffer_stage1 = io.BytesIO()
    with zipfile.ZipFile(zip_buffer_stage1, "w") as zf:
        for fname, fdata in st.session_state.stage1_files.items():
            zf.writestr(fname, fdata["content"].encode("utf-8"))

    st.download_button(
        label="📥 第1段階: 変換済み標準Markdown Zipをダウンロード",
        data=zip_buffer_stage1.getvalue(),
        file_name="reiki_standard_markdown.zip",
        mime="application/zip",
    )

st.divider()


# ==============================================================================
# 【第2段階】無償API（Gemini）による要約・タグの自動付与
# ==============================================================================
st.header("【第2段階】要約・タグの自動付与（Gemini API）")

if not st.session_state.stage1_files:
    st.warning(
        "⚠️ 先に【第1段階】の変換処理を実行してデータを生成してください。"
    )
elif not api_key:
    st.error(
        "❌ Streamlit Secrets に `GEMINI_API_KEY` が設定されていません。Settings > Secrets を確認してください。"
    )
else:
    client = genai.Client(api_key=api_key)

    if st.button("🚀 第2段階: 要約の自動付与を開始する"):
        progress_bar = st.progress(0)
        status_area = st.empty()
        time_area = st.empty()

        start_time = time.time()
        processed_rules_count = 0
        total_rules_count = sum(
            v["rule_count"] for v in st.session_state.stage1_files.values()
        )
        final_files = {}

        # 要約生成関数（新SDK呼び出し）
        def get_summary(title, text_content):
            prompt = f"""
あなたは自治体例規の整理補助AIです。
以下の例規の「タイトル」と「本文」を読み、概要とカテゴリを以下のフォーマットで短く出力してください。

【対象例規】
タイトル: {title}
本文冒頭: {text_content[:1000]}

【出力フォーマット】
概要：[1〜2文で何について定めたものか] / 対象カテゴリ：[関連する検索単語や分野をカンマ区切りで3〜5個]
"""
            try:
                response = client.models.generate_content(
                    model="gemini-2.0-flash",
                    contents=prompt,
                )
                time.sleep(4.0)  # 無償枠のレート制限対策（1分間15回まで）
                return response.text.strip().replace("\n", " ")
            except Exception as e:
                time.sleep(8.0)
                return "概要の自動生成に失敗しました"

        # 処理ループ
        for fname, fdata in st.session_state.stage1_files.items():
            text = fdata["content"]
            sections = re.split(r"\n(?=##\s+)", text)
            new_sections = []

            for section in sections:
                match = re.search(r"^##\s+(.+)$", section, re.MULTILINE)
                if match:
                    rule_title = match.group(1).strip()
                    processed_rules_count += 1

                    # 時間・進捗計算
                    elapsed = time.time() - start_time
                    avg_time_per_item = (
                        elapsed / processed_rules_count
                        if processed_rules_count > 0
                        else 4.5
                    )
                    remaining_items = total_rules_count - processed_rules_count
                    remaining_seconds = remaining_items * avg_time_per_item

                    rem_min = int(remaining_seconds // 60)
                    rem_sec = int(remaining_seconds % 60)

                    # 画面表示のリアルタイム更新
                    status_area.markdown(
                        f"**処理中 ({processed_rules_count}/{total_rules_count} 件):** `{fname}` ➔ `## {rule_title}`"
                    )
                    time_area.markdown(
                        f"⏱ 経過時間: **{int(elapsed)}秒** | 🏁 残り予想時間: **約 {rem_min}分 {rem_sec}秒**"
                    )

                    # API呼び出し
                    summary = get_summary(rule_title, section)
                    summary_tag = f"<!-- summary: {summary} -->\n"
                    section = summary_tag + section

                    # プログレスバー更新
                    progress_bar.progress(
                        processed_rules_count / total_rules_count
                    )

                new_sections.append(section)

            final_files[fname] = "\n".join(new_sections)

        st.success("🎉 すべての例規への要約付与が完了しました！")

        # 第2段階の完成Zipファイルの作成・ダウンロード
        zip_buffer_stage2 = io.BytesIO()
        with zipfile.ZipFile(zip_buffer_stage2, "w") as zf:
            for fname, fcontent in final_files.items():
                zf.writestr(fname, fcontent.encode("utf-8"))

        st.download_button(
            label="📦 第2段階: 完成したナレッジZipファイルをダウンロード",
            data=zip_buffer_stage2.getvalue(),
            file_name="reiki_knowledge_summary_added.zip",
            mime="application/zip",
        )
