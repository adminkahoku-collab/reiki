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


# --- 2. API Key の取得 ---
api_key = None
if "GEMINI_API_KEY" in st.secrets:
    api_key = st.secrets["GEMINI_API_KEY"]


# --- 3. 例規HTML解析・補助関数 ---
def convert_html_to_clean_text(html_content: str) -> tuple[str, str]:
    """例規HTMLからタイトルと本文テキストを抽出"""
    soup = BeautifulSoup(html_content, "html.parser")
    for element in soup(["script", "style", "meta", "link"]):
        element.decompose()

    title_tag = soup.find(["h1", "h2", "title"])
    title = title_tag.get_text(strip=True) if title_tag else "無題の例規"

    text = soup.get_text(separator="\n")
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    body_text = "\n".join(lines)

    return title, body_text


# セッション状態の初期化
if "stage1_hen_files" not in st.session_state:
    st.session_state.stage1_hen_files = {}


# --- 4. メイン画面 ---
st.title("📄 例規データ ナレッジ化処理システム")
st.caption(
    "【第1段階】DVD Zip ➔ 13編別統合MD生成 ｜ 【第2段階】編選択 ➔ Gemini要約付与"
)

tab1, tab2 = st.tabs(["【第1段階】DVD ➔ 13編別MD作成", "【第2段階】編選択 ➔ 要約タグ付与"])


# ==============================================================================
# 【第1段階】DVD Zip ➔ 13編の統合 Markdown ファイル生成
# ==============================================================================
with tab1:
    st.header("第1段階: 13編別の統合Markdownファイル生成")

    uploaded_dvd_zip = st.file_uploader(
        "DVDデータ（HTML等）がまとまった Zip ファイルを選択してください",
        type=["zip"],
        key="dvd_zip_uploader",
    )

    if uploaded_dvd_zip:
        if st.button("⚙️ 13編のMarkdown化を実行", key="btn_stage1"):
            # Zip内のすべてのHTMLファイルを一旦メモリにキャッシュ
            html_cache = {}
            bunya_files = {}

            with zipfile.ZipFile(uploaded_dvd_zip, "r") as z:
                for zip_info in z.infolist():
                    if zip_info.filename.startswith("__MACOSX"):
                        continue

                    fname = os.path.basename(zip_info.filename)
                    if fname.lower().endswith(
                        (".html", ".htm")
                    ):
                        with z.open(zip_info) as f:
                            content_bytes = f.read()
                            try:
                                html_text = content_bytes.decode("cp932")
                            except UnicodeDecodeError:
                                html_text = content_bytes.decode(
                                    "utf-8", errors="ignore"
                                )

                            html_cache[fname] = html_text
                            if fname.startswith("bunya_"):
                                bunya_files[fname] = html_text

            # 体系目次（bunya_*.html）から編（大分類）とリンク先の例規を抽出
            # 13編の集計構造を構築
            hen_data = {}  # { "第1編 総規": [ (title, content), ... ] }

            # bunya_ファイルを目次順に並べ替え
            sorted_bunya = sorted(bunya_files.items(), key=lambda x: x[0])

            current_hen_title = "その他"

            for bunya_name, bunya_html in sorted_bunya:
                soup = BeautifulSoup(bunya_html, "html.parser")

                # 目次内の「第X編 XXX」という大分類見出しを探す
                text_content = soup.get_text()
                match = re.search(r"(第\d+編\s*[^ \n\r\t]+)", text_content)
                if match:
                    current_hen_title = match.group(1).strip()

                if current_hen_title not in hen_data:
                    hen_data[current_hen_title] = []

                # bunyaファイル内の例規リンク（H..._J.html）を抽出
                links = soup.find_all("a", href=True)
                for a in links:
                    href = os.path.basename(a["href"])
                    if (
                        href.startswith("H")
                        and href.lower().endswith(
                            ("_j.html", "_j.htm")
                        )
                        and href in html_cache
                    ):
                        rule_title, rule_body = convert_html_to_clean_text(
                            html_cache[href]
                        )
                        hen_data[current_hen_title].append(
                            (rule_title, rule_body)
                        )

            # 万が一bunya解析で拾えなかったH..._Jファイルを予備的に集計
            processed_hrefs = {
                item[0]
                for items in hen_data.values()
                for item in items
            }

            # 13編の統合Markdownファイルテキストを構築
            compiled_hen_files = {}
            for idx, (hen_name, rules) in enumerate(hen_data.items(), start=1):
                if not rules:
                    continue

                # ファイル名: 01_第1編_総規.md
                clean_hen_name = re.sub(r'[\\/:*?"<>|]', "_", hen_name)
                md_filename = f"{idx:02d}_{clean_hen_name}.md"

                # ドキュメント構造: 大分類=#, 例規毎=##, 例規区切り=---
                md_lines = [f"# {hen_name}\n"]

                for title, body in rules:
                    md_lines.append(f"## {title}\n")
                    md_lines.append(f"{body}\n")
                    md_lines.append("\n---\n")

                full_md_text = "\n".join(md_lines)
                compiled_hen_files[md_filename] = {
                    "hen_name": hen_name,
                    "rule_count": len(rules),
                    "content": full_md_text,
                    "length": len(full_md_text),
                }

            st.session_state.stage1_hen_files = compiled_hen_files
            st.success(
                f"✅ **全 {len(compiled_hen_files)} 編** の統合Markdownファイルを生成しました！"
            )

    # 生成された13編の一覧・ボリューム表示
    if st.session_state.stage1_hen_files:
        st.subheader("📊 13編のMarkdownファイル成果物一覧")

        # Zipダウンロードボタン
        zip_buffer_stage1 = io.BytesIO()
        with zipfile.ZipFile(zip_buffer_stage1, "w") as zf:
            for fname, fdata in st.session_state.stage1_hen_files.items():
                zf.writestr(fname, fdata["content"].encode("utf-8"))

        st.download_button(
            label="📥 13編すべての統合Markdown Zipをダウンロード",
            data=zip_buffer_stage1.getvalue(),
            file_name="reiki_13_hen_markdown.zip",
            mime="application/zip",
        )

        # 画面一覧表示（ファイル名、編名、収録例規数、文字数ボリューム）
        summary_table = [
            {
                "ファイル名": fname,
                "大分類（編）": fdata["hen_name"],
                "収録例規数": f"{fdata['rule_count']} 件",
                "文字数 (ボリューム)": f"{fdata['length']:,} 文字",
            }
            for fname, fdata in st.session_state.stage1_hen_files.items()
        ]
        st.dataframe(summary_table, use_container_width=True)


# ==============================================================================
# 【第2段階】13編のMDから選択して要約を付与
# ==============================================================================
with tab2:
    st.header("第2段階: 編別Markdownファイルを選択して要約付与")

    target_files = {}

    if st.session_state.stage1_hen_files:
        st.info("💡 第1段階で生成した13編のデータがメモリ内に保持されています。")
        target_files = st.session_state.stage1_hen_files

    # 外部Zipのアップロードにも対応
    uploaded_md_zip = st.file_uploader(
        "（任意）事前にダウンロードした 13編 Zip ファイルを使う場合はこちらを選択してください",
        type=["zip"],
        key="md_zip_uploader",
    )

    if uploaded_md_zip:
        zip_files = {}
        with zipfile.ZipFile(uploaded_md_zip, "r") as z:
            for zip_info in z.infolist():
                if zip_info.filename.startswith("__MACOSX"):
                    continue
                if zip_info.filename.endswith(".md"):
                    with z.open(zip_info) as f:
                        fname = os.path.basename(zip_info.filename)
                        content = f.read().decode("utf-8")
                        zip_files[fname] = {
                            "hen_name": fname.replace(".md", ""),
                            "rule_count": content.count("\n## "),
                            "content": content,
                            "length": len(content),
                        }
        target_files = zip_files
        st.success(
            f"📁 Zipから **{len(target_files)} 件** の編別Markdownファイルを読み込みました。"
        )

    if not target_files:
        st.warning(
            "⚠️ 処理対象のデータがありません。先に第1段階を実行するか、13編Zipをアップロードしてください。"
        )
    else:
        st.subheader("🎯 要約処理を実行する「編」の選択")

        # 選択ボックス用オプション作成
        file_options = {
            f"{fname} （{fdata['hen_name']} / {fdata['length']:,}文字）": fname
            for fname, fdata in target_files.items()
        }

        col_btn1, col_btn2, _ = st.columns([1, 1, 4])
        select_all = col_btn1.button("全選択")
        deselect_all = col_btn2.button("全解除")

        if "selected_hen_files" not in st.session_state or select_all:
            st.session_state.selected_hen_files = list(target_files.keys())
        elif deselect_all:
            st.session_state.selected_hen_files = []

        default_keys = [
            f"{fname} （{target_files[fname]['hen_name']} / {target_files[fname]['length']:,}文字）"
            for fname in st.session_state.selected_hen_files
            if fname in target_files
        ]

        selected_displays = st.multiselect(
            "要約タグを付与する編ファイルを選択してください（テストとして特定の1編のみ選択することも可能です）",
            options=list(file_options.keys()),
            default=default_keys,
        )

        selected_fnames = [file_options[disp] for disp in selected_displays]
        st.session_state.selected_hen_files = selected_fnames

        selected_count = len(selected_fnames)
        col_m1, col_m2 = st.columns(2)
        col_m1.metric("選択中の編数", f"{selected_count} / {len(target_files)} 編")

        if selected_count > 0:
            if not api_key:
                st.error(
                    "❌ Streamlit Secrets に `GEMINI_API_KEY` が設定されていません。"
                )
            else:
                client = genai.Client(api_key=api_key)

                if st.button(
                    f"🚀 選択した {selected_count} 編に要約・タグを自動付与する",
                    key="btn_stage2",
                ):
                    progress_bar = st.progress(0)
                    status_area = st.empty()
                    time_area = st.empty()

                    start_time = time.time()
                    processed_count = 0
                    final_hen_files = {}

                    def get_summary_with_retry(
                        md_content, max_retries=3
                    ) -> str:
                        prompt = f"""
あなたは自治体例規の整理補助AIです。
以下の例規編テキストを読み、この編全体の概要と主要なカテゴリを以下のフォーマットで短く出力してください。

【対象例規データ抜粋】
{md_content[:2000]}

【出力フォーマット】
概要：[1〜2文でどのような分野の例規が収められているか] / 対象カテゴリ：[関連する検索単語や分野をカンマ区切りで3〜5個]
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
                        fdata = target_files[fname]
                        md_text = fdata["content"]
                        processed_count += 1

                        elapsed = time.time() - start_time
                        avg_time = (
                            elapsed / processed_count
                            if processed_count > 0
                            else 5.0
                        )
                        rem_sec = int(
                            (selected_count - processed_count) * avg_time
                        )
                        rem_min = rem_sec // 60
                        rem_sec = rem_sec % 60

                        status_area.markdown(
                            f"**処理中 ({processed_count}/{selected_count} 編):** `{fname}`"
                        )
                        time_area.markdown(
                            f"⏱ 経過時間: **{int(elapsed)}秒** | 🏁 残り予想時間: **約 {rem_min}分 {rem_sec}秒**"
                        )

                        if md_text.startswith("<!-- summary:"):
                            final_hen_files[fname] = md_text
                        else:
                            summary = get_summary_with_retry(md_text)
                            summary_tag = f"<!-- summary: {summary} -->\n"
                            final_hen_files[fname] = summary_tag + md_text

                        progress_bar.progress(processed_count / selected_count)

                    st.success(
                        f"🎉 選択した {selected_count} 編への要約付与が完了しました！"
                    )

                    # 完成版Zipダウンロード
                    zip_buffer_stage2 = io.BytesIO()
                    with zipfile.ZipFile(zip_buffer_stage2, "w") as zf:
                        for fname, fcontent in final_hen_files.items():
                            zf.writestr(fname, fcontent.encode("utf-8"))

                    st.download_button(
                        label="📦 完成版13編ナレッジZipをダウンロード",
                        data=zip_buffer_stage2.getvalue(),
                        file_name="reiki_13_hen_knowledge_summary.zip",
                        mime="application/zip",
                    )
