import io
import os
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


# --- 2. API Key の取得 ---
api_key = None
if "GEMINI_API_KEY" in st.secrets:
    api_key = st.secrets["GEMINI_API_KEY"]


# --- 3. 補助関数 ---
def is_reiki_file(filename: str) -> bool:
    """DVDのファイル名パターンに基づく例規判定"""
    base_name = os.path.basename(filename)
    if base_name.startswith("bunya_"):
        return True
    base_lower = base_name.lower()
    if base_name.startswith("H") and (
        base_lower.endswith("_j.html") or base_lower.endswith("_j.htm")
    ):
        return True
    return False


def convert_html_to_markdown(html_content: str) -> tuple[str, str]:
    """例規HTMLからタイトルと本文を抽出し、標準Markdownを生成"""
    soup = BeautifulSoup(html_content, "html.parser")
    for element in soup(["script", "style", "meta", "link"]):
        element.decompose()

    title_tag = soup.find(["h1", "h2", "title"])
    title = title_tag.get_text(strip=True) if title_tag else "無題の例規"

    text = soup.get_text(separator="\n")
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    body_text = "\n".join(lines)

    md_content = f"## {title}\n\n{body_text}\n"
    return title, md_content


# セッション状態の初期化
if "stage1_files" not in st.session_state:
    st.session_state.stage1_files = {}


# --- 4. メイン画面 ---
st.title("📄 例規データ ナレッジ化処理システム")
st.caption("【第1段階】DVD Zip(HTML) ➔ MD変換 ｜ 【第2段階】MDファイル選択 ➔ Gemini要約付与")

tab1, tab2 = st.tabs(["【第1段階】HTML ➔ MD変換", "【第2段階】MD選択 ➔ 要約タグ付与"])


# ==============================================================================
# 【第1段階】DVD Zip (HTML等) ➔ 標準 Markdown 変換 & ボリューム確認
# ==============================================================================
with tab1:
    st.header("第1段階: DVD ZipファイルのMarkdown変換")

    uploaded_dvd_zip = st.file_uploader(
        "DVDデータ（HTML等）がまとまった Zip ファイルを選択してください",
        type=["zip"],
        key="dvd_zip_uploader",
    )

    if uploaded_dvd_zip:
        if st.button("⚙️ Markdown変換を実行", key="btn_stage1"):
            stage1_output = {}
            skipped_count = 0

            with zipfile.ZipFile(uploaded_dvd_zip, "r") as z:
                for zip_info in z.infolist():
                    if zip_info.filename.startswith("__MACOSX"):
                        continue

                    if is_reiki_file(zip_info.filename):
                        with z.open(zip_info) as f:
                            content_bytes = f.read()
                            try:
                                html_text = content_bytes.decode("cp932")
                            except UnicodeDecodeError:
                                html_text = content_bytes.decode(
                                    "utf-8", errors="ignore"
                                )

                            rule_title, md_text = convert_html_to_markdown(
                                html_text
                            )
                            md_filename = (
                                os.path.basename(zip_info.filename).rsplit(
                                    ".", 1
                                )[0]
                                + ".md"
                            )

                            stage1_output[md_filename] = {
                                "title": rule_title,
                                "content": md_text,
                                "length": len(md_text),
                            }
                    else:
                        skipped_count += 1

            st.session_state.stage1_files = stage1_output
            st.success(
                f"✅ 抽出完了: **{len(stage1_output)} 件** の例規をMarkdown化しました。（対象外 {skipped_count} 件除外）"
            )

    if st.session_state.stage1_files:
        st.subheader("📊 第1段階の変換成果物一覧")

        # Zipダウンロードボタン
        zip_buffer_stage1 = io.BytesIO()
        with zipfile.ZipFile(zip_buffer_stage1, "w") as zf:
            for fname, fdata in st.session_state.stage1_files.items():
                zf.writestr(fname, fdata["content"].encode("utf-8"))

        st.download_button(
            label="📥 第1段階の全MDファイルをZipでダウンロード",
            data=zip_buffer_stage1.getvalue(),
            file_name="reiki_standard_markdown.zip",
            mime="application/zip",
        )

        # ファイルごとのボリューム（文字数）表示
        file_data_list = [
            {
                "ファイル名": fname,
                "タイトル": fdata["title"],
                "文字数 (ボリューム)": f"{fdata['length']:,} 文字",
            }
            for fname, fdata in st.session_state.stage1_files.items()
        ]
        st.dataframe(file_data_list, use_container_width=True)


# ==============================================================================
# 【第2段階】MDファイルを選択して要約を付与
# ==============================================================================
with tab2:
    st.header("第2段階: Markdownファイルへの要約・タグ自動付与")

    # データソースの確保（メモリ上 または Zipアップロード）
    target_md_files = {}

    if st.session_state.stage1_files:
        st.info("💡 第1段階で変換したデータがメモリ内に保持されています。")
        target_md_files = {
            fname: fdata["content"]
            for fname, fdata in st.session_state.stage1_files.items()
        }

    # 事前にダウンロードしたZipがある場合のアップローダー
    uploaded_md_zip = st.file_uploader(
        "（任意）手元の Markdown Zip ファイルを読み込んで処理する場合はこちらを選択してください",
        type=["zip"],
        key="md_zip_uploader",
    )

    if uploaded_md_zip:
        zip_md_files = {}
        with zipfile.ZipFile(uploaded_md_zip, "r") as z:
            for zip_info in z.infolist():
                if zip_info.filename.startswith("__MACOSX"):
                    continue
                if zip_info.filename.endswith(".md"):
                    with z.open(zip_info) as f:
                        fname = os.path.basename(zip_info.filename)
                        zip_md_files[fname] = f.read().decode("utf-8")
        target_md_files = zip_md_files
        st.success(
            f"📁 Zipから **{len(target_md_files)} 件** のMarkdownファイルを読み込みました。"
        )

    if not target_md_files:
        st.warning(
            "⚠️ 処理対象のデータがありません。第1段階を実行するか、Markdown Zipをアップロードしてください。"
        )
    else:
        st.subheader("🎯 処理対象ファイルの選択")

        # 全選択/全解除ボタン
        col_btn1, col_btn2, _ = st.columns([1, 1, 4])
        select_all = col_btn1.button("全選択")
        deselect_all = col_btn2.button("全解除")

        if "selected_files" not in st.session_state or select_all:
            st.session_state.selected_files = list(target_md_files.keys())
        elif deselect_all:
            st.session_state.selected_files = []

        # ファイルごとのボリューム表示付きマルチセレクトボックス
        file_options = {
            f"{fname}  ({len(content):,}文字)": fname
            for fname, content in target_md_files.items()
        }

        # デフォルト選択値の整合性チェック
        current_default_keys = [
            f"{fname}  ({len(target_md_files[fname]):,}文字)"
            for fname in st.session_state.selected_files
            if fname in target_md_files
        ]

        selected_display_names = st.multiselect(
            "要約処理を実行するファイルを選択してください（テストで1〜2件のみ選択することも可能です）",
            options=list(file_options.keys()),
            default=current_default_keys,
        )

        selected_fnames = [
            file_options[disp_name] for disp_name in selected_display_names
        ]
        st.session_state.selected_files = selected_fnames

        selected_count = len(selected_fnames)
        estimated_seconds = selected_count * 4.5
        est_min = int(estimated_seconds // 60)
        est_sec = int(estimated_seconds % 60)

        col_m1, col_m2 = st.columns(2)
        col_m1.metric("選択中のファイル数", f"{selected_count} / {len(target_md_files)} 件")
        col_m2.metric("予想処理時間", f"約 {est_min}分 {est_sec}秒")

        if selected_count > 0:
            if not api_key:
                st.error(
                    "❌ Streamlit Secrets に `GEMINI_API_KEY` が設定されていません。"
                )
            else:
                client = genai.Client(api_key=api_key)

                if st.button(
                    f"🚀 選択した {selected_count} 件に要約・タグを自動付与する",
                    key="btn_stage2",
                ):
                    progress_bar = st.progress(0)
                    status_area = st.empty()
                    time_area = st.empty()

                    start_time = time.time()
                    processed_count = 0
                    final_files = {}

                    def get_summary_with_retry(
                        md_content, max_retries=3
                    ) -> str:
                        prompt = f"""
あなたは自治体例規の整理補助AIです。
以下の例規Markdownデータを読み、概要とカテゴリを以下のフォーマットで短く出力してください。

【対象例規Markdown】
{md_content[:1500]}

【出力フォーマット】
概要：[1〜2文で何について定めたものか] / 対象カテゴリ：[関連する検索単語や分野をカンマ区切りで3〜5個]
"""
                        for attempt in range(max_retries):
                            try:
                                response = client.models.generate_content(
                                    model="gemini-2.0-flash",
                                    contents=prompt,
                                )
                                time.sleep(4.5)
                                return response.text.strip().replace("\n", " ")
                            except Exception:
                                if attempt < max_retries - 1:
                                    time.sleep((attempt + 1) * 10)
                                else:
                                    return (
                                        "概要の自動生成に失敗しました（API制限）"
                                    )

                    for fname in selected_fnames:
                        md_text = target_md_files[fname]
                        processed_count += 1

                        elapsed = time.time() - start_time
                        avg_time = (
                            elapsed / processed_count
                            if processed_count > 0
                            else 4.5
                        )
                        rem_sec = int(
                            (selected_count - processed_count) * avg_time
                        )
                        rem_min = rem_sec // 60
                        rem_sec = rem_sec % 60

                        status_area.markdown(
                            f"**処理中 ({processed_count}/{selected_count} 件):** `{fname}`"
                        )
                        time_area.markdown(
                            f"⏱ 経過時間: **{int(elapsed)}秒** | 🏁 残り予想時間: **約 {rem_min}分 {rem_sec}秒**"
                        )

                        # 既に要約タグがある場合はスキップして二重タグ防止
                        if md_text.startswith("<!-- summary:"):
                            final_files[fname] = md_text
                        else:
                            summary = get_summary_with_retry(md_text)
                            summary_tag = f"<!-- summary: {summary} -->\n"
                            final_files[fname] = summary_tag + md_text

                        progress_bar.progress(processed_count / selected_count)

                    st.success(
                        f"🎉 選択した {selected_count} 件への要約付与が完了しました！"
                    )

                    # 完成版Zipダウンロード
                    zip_buffer_stage2 = io.BytesIO()
                    with zipfile.ZipFile(zip_buffer_stage2, "w") as zf:
                        for fname, fcontent in final_files.items():
                            zf.writestr(fname, fcontent.encode("utf-8"))

                    st.download_button(
                        label="📦 処理済みナレッジZip（reiki_knowledge_selected.zip）をダウンロード",
                        data=zip_buffer_stage2.getvalue(),
                        file_name="reiki_knowledge_selected.zip",
                        mime="application/zip",
                    )
