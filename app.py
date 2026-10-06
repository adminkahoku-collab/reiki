import io
import re
import zipfile
from bs4 import BeautifulSoup
from google import genai
import streamlit as st

st.set_page_config(
    page_title="例規13編分割＆要約作成ツール", page_icon="⚖", layout="wide"
)
st.title("⚖️ 例規13編ファイル分割・要約作成ツール")

# --- 設定サイドバー ---
st.sidebar.header("🔑 認証設定")
password = st.sidebar.text_input("職員用パスワード", type="password")

# --- Streamlit Secrets から Gemini API キーを取得 ---
gemini_api_key = st.secrets.get("GEMINI_API_KEY", "")


# --- Geminiによる例規ごとの要約生成関数 ---
def generate_summary_with_gemini(
    title: str, content: str, api_key: str
) -> str:
    """Gemini を使用して例規ごとの要約を生成"""
    if not api_key:
        return "・（APIキー未設定のため要約スキップ）"

    # 長すぎる本文を一定文字数に制限（トークン節約および応答速度向上のため）
    trimmed_content = content[:2500].strip()

    prompt = f"""
あなたは自治体職員向けの例規要約アシスタントです。
以下の例規の本文を読み、主要なポイントや対象者、重要な手続きを箇条書きで3行程度で簡潔に要約してください。

【出力条件】
- 必ず箇条書き（・）で3点以内にまとめてください。
- タイトルや挨拶、前置き（例:「以下は要約です」など）は含めず、箇条書き本文のみを出力してください。

【対象例規】
タイトル: {title}
本文:
{trimmed_content}
"""
    try:
        client = genai.Client(api_key=api_key)
        response = client.models.generate_content(
            model="gemini-3.8-flash",
            contents=prompt,
        )
        return response.text.strip()
    except Exception as e:
        st.warning(f"Gemini API（要約生成）でエラー ({title}): {e}")
        return "・（要約生成エラーが発生しました）"


# --- 単一のMarkdownファイル内の全例規に要約を付与する関数 ---
def process_markdown_summaries(
    md_text: str, api_key: str, progress_callback=None
) -> str:
    """Markdownファイル全体を解析し、各例規の見出し直下に要約ブロックを挿入する"""
    # 例規ごとに分割（"---" 区切り、または "## " で区切られている構造に対応）
    sections = md_text.split("\n\n---\n\n")
    processed_sections = []

    total_sections = len(sections)

    for idx, sec in enumerate(sections):
        sec_str = sec.strip()
        if not sec_str:
            continue

        # "## " で始まる見出しを探す
        lines = sec_str.splitlines()
        rule_title = None
        title_line_idx = -1

        for i, line in enumerate(lines):
            if line.startswith("## "):
                rule_title = line.replace("## ", "").strip()
                title_line_idx = i
                break

        # 見出しが存在し、本文がある場合のみ要約を生成
        if rule_title and title_line_idx != -1:
            body_lines = lines[title_line_idx + 1 :]
            body_text = "\n".join(body_lines).strip()

            # 本文が存在する場合に要約処理を実施
            if body_text:
                summary = generate_summary_with_gemini(
                    rule_title, body_text, api_key
                )

                # 引用ブロック（>）形式で概要・要約を整形
                summary_block = "> **【概要・要約】**\n" + "\n".join(
                    [
                        f"> {line}"
                        for line in summary.splitlines()
                        if line.strip()
                    ]
                )

                # 見出しの直下に要約ブロックを挿入
                new_sec = (
                    "\n".join(lines[: title_line_idx + 1])
                    + "\n\n"
                    + summary_block
                    + "\n\n"
                    + "\n".join(lines[title_line_idx + 1 :])
                )
                processed_sections.append(new_sec)
            else:
                processed_sections.append(sec_str)
        else:
            # ## 見出しがないセクション（ファイル冒頭の # 01_第１編 総規 など）
            processed_sections.append(sec_str)

        if progress_callback:
            progress_callback(idx + 1, total_sections)

    return "\n\n---\n\n".join(processed_sections)


# --- メイン処理 ---
if password == "reiki063215":
    st.success("認証されました。")

    if not gemini_api_key:
        st.info(
            "💡 .streamlit/secrets.toml に GEMINI_API_KEY "
            "が設定されていない場合、要約生成はスキップされます。"
        )

    # ==========================================
    # ステップ1: 元データ(ZIP)から13編Markdownを作成
    # ==========================================
    st.header("ステップ1: 元データ(ZIP)から13編Markdownを作成")
    uploaded_html_zip = st.file_uploader(
        "元データのZIPファイルをアップロードしてください",
        type=["zip"],
        key="step1_zip",
    )

    if uploaded_html_zip is not None:
        if st.button("⚖️ 1. 例規マークダウン作成（13編分割）"):
            try:
                zip_buffer = io.BytesIO(uploaded_html_zip.read())
                with zipfile.ZipFile(zip_buffer, "r") as z:
                    all_files = z.namelist()
                    target_bunya = next(
                        (
                            f
                            for f in all_files
                            if f.split("/")[-1].lower()
                            in ["bunya_0010000.html", "bunya0010000.html"]
                        ),
                        None,
                    )
                    j_files = {
                        f.split("/")[-1]
                        .replace("_J.html", "")
                        .replace("_j.html", ""): f
                        for f in all_files
                        if f.lower().endswith("_j.html")
                    }

                    if target_bunya:
                        bunya_bytes = z.read(target_bunya)
                        try:
                            bunya_html = bunya_bytes.decode("cp932")
                        except UnicodeDecodeError:
                            bunya_html = bunya_bytes.decode(
                                "utf-8", errors="ignore"
                            )

                        bunya_soup = BeautifulSoup(bunya_html, "html.parser")
                        re_link = re.compile(r"OpenResDataWin\('([^']+)'\)")
                        re_hen = re.compile(
                            r"第\s*[0-9０-９一二三四五六七八九十]+\s*編\s*.*"
                        )

                        hen_data = {}
                        current_hen = "00_未分類"
                        hen_counter = 0

                        for elem in bunya_soup.find_all(
                            ["strong", "tr", "p", "div"]
                        ):
                            if elem.name == "strong":
                                lines = [
                                    line.strip()
                                    for line in elem.get_text(
                                        "\n", strip=True
                                    ).split("\n")
                                    if line.strip()
                                ]
                                for line in lines:
                                    hen_match = re_hen.search(line)
                                    if hen_match:
                                        clean_text = re.sub(
                                            r'[\\/:*?"<>|]',
                                            "_",
                                            hen_match.group(0).strip(),
                                        )
                                        if not current_hen.endswith(
                                            clean_text
                                        ):
                                            hen_counter += 1
                                            current_hen = f"{hen_counter:02d}_{clean_text}"
                                            if current_hen not in hen_data:
                                                hen_data[current_hen] = []

                            elif elem.name == "tr":
                                for a_tag in elem.find_all("a"):
                                    onclick_attr = a_tag.get(
                                        "href", ""
                                    ) or a_tag.get("onclick", "")
                                    match = re_link.search(onclick_attr)
                                    if match:
                                        doc_id = match.group(1)
                                        title = a_tag.get_text(strip=True)
                                        if title and doc_id:
                                            if current_hen not in hen_data:
                                                hen_data[current_hen] = []
                                            if not any(
                                                d["id"] == doc_id
                                                for d in hen_data[current_hen]
                                            ):
                                                hen_data[current_hen].append(
                                                    {
                                                        "id": doc_id,
                                                        "title": title,
                                                    }
                                                )

                        if (
                            "00_未分類" in hen_data
                            and not hen_data["00_未分類"]
                        ):
                            del hen_data["00_未分類"]

                        output_zip_buffer = io.BytesIO()
                        progress_bar = st.progress(0)
                        status_text = st.empty()
                        total_hens = len(hen_data)
                        current_hen_idx = 0

                        with zipfile.ZipFile(
                            output_zip_buffer, "w", zipfile.ZIP_DEFLATED
                        ) as out_zip:
                            for hen_name, items in hen_data.items():
                                current_hen_idx += 1
                                status_text.text(
                                    f"処理中 ({current_hen_idx}/{total_hens}): {hen_name}"
                                )
                                progress_bar.progress(
                                    current_hen_idx / total_hens
                                )

                                if not items:
                                    continue

                                hen_markdown = f"# {hen_name}\n\n"
                                rule_blocks = []
                                for item in items:
                                    doc_id = item["id"]
                                    rule_title = item["title"]

                                    if doc_id in j_files:
                                        j_bytes = z.read(j_files[doc_id])
                                        try:
                                            j_html = j_bytes.decode("cp932")
                                        except UnicodeDecodeError:
                                            j_html = j_bytes.decode(
                                                "utf-8", errors="ignore"
                                            )

                                        j_soup = BeautifulSoup(
                                            j_html, "html.parser"
                                        )
                                        for tag in j_soup(
                                            ["script", "style", "noscript"]
                                        ):
                                            tag.decompose()

                                        raw_text = j_soup.get_text(
                                            separator="\n", strip=True
                                        )
                                        lines = [
                                            line.strip()
                                            for line in raw_text.splitlines()
                                            if line.strip()
                                        ]
                                        title_pattern = re.compile(
                                            rf"^(○)?{re.escape(rule_title)}$"
                                        )
                                        cleaned_lines = [
                                            line
                                            for line in lines
                                            if not title_pattern.match(line)
                                        ]
                                        cleaned_text = "\n".join(cleaned_lines)

                                        rule_blocks.append(
                                            f"## {rule_title}\n\n{cleaned_text}"
                                        )

                                hen_markdown += "\n\n---\n\n".join(rule_blocks)
                                out_zip.writestr(
                                    f"{hen_name}.md",
                                    hen_markdown.encode("utf-8"),
                                )

                        st.success(
                            "🎉 ステップ1完了！13編のMarkdownを作成しました。"
                        )
                        st.download_button(
                            label="📥 13編分割済みMarkdown (ZIP) をダウンロード",
                            data=output_zip_buffer.getvalue(),
                            file_name="reiki_13hen_markdowns.zip",
                            mime="application/zip",
                        )
            except Exception as e:
                st.error(f"エラーが発生しました: {str(e)}")

    st.markdown("---")

    # ==========================================
    # ステップ2: ダウンロードしたZIPを指定して要約付与
    # ==========================================
    st.header("ステップ2: 分割済みZIPを指定して例規ごとに要約を付与")
    uploaded_md_zip = st.file_uploader(
        "ステップ1でダウンロードした Markdown ZIPファイルを指定してください",
        type=["zip"],
        key="step2_zip",
    )

    if uploaded_md_zip is not None:
        try:
            zip_buffer = io.BytesIO(uploaded_md_zip.read())
            md_dict = {}
            with zipfile.ZipFile(zip_buffer, "r") as z:
                for filename in z.namelist():
                    if filename.endswith(".md"):
                        content = z.read(filename).decode("utf-8")
                        hen_key = filename.replace(".md", "")
                        md_dict[hen_key] = content

            st.info(
                f"📁 ZIPから {len(md_dict)} 個の「編」ファイルを読み込みました。"
            )

            # 処理対象の「編」を選択するUI
            st.subheader("🎯 処理対象のデータ（編）を指定")
            select_all = st.checkbox("すべての編を選択する", value=True)
            available_keys = sorted(list(md_dict.keys()))
            selected_keys = st.multiselect(
                "要約を付与したい「編」を選択してください:",
                options=available_keys,
                default=available_keys if select_all else [],
            )

            if st.button("🤖 2. 選択した編の例規ごとに要約を付与する"):
                if not selected_keys:
                    st.warning("処理対象の編が選択されていません。")
                else:
                    progress_bar = st.progress(0.0)
                    status_text = st.empty()

                    updated_md_dict = md_dict.copy()
                    total_selected = len(selected_keys)

                    for idx, key in enumerate(selected_keys):
                        status_text.text(
                            f"【編 {idx+1}/{total_selected}】要約処理中: {key}"
                        )

                        content = md_dict[key]

                        # 各ファイル内の例規要約処理を実行
                        def update_sub_progress(current_sec, total_sec):
                            sub_p = (
                                idx + (current_sec / total_sec)
                            ) / total_selected
                            progress_bar.progress(min(sub_p, 1.0))

                        updated_content = process_markdown_summaries(
                            content,
                            gemini_api_key,
                            progress_callback=update_sub_progress,
                        )

                        updated_md_dict[key] = updated_content

                    # ナレッジ用ベースデータのZIP生成
                    out_zip_buffer = io.BytesIO()
                    with zipfile.ZipFile(
                        out_zip_buffer, "w", zipfile.ZIP_DEFLATED
                    ) as out_zip:
                        for k, v in updated_md_dict.items():
                            out_zip.writestr(f"{k}.md", v.encode("utf-8"))

                    st.success(
                        "✨ 例規ごとの要約付与が完了しました！ナレッジベースデータを出力できます。"
                    )
                    st.download_button(
                        label="📥 ナレッジベースデータ (要約付きZIP) をダウンロード",
                        data=out_zip_buffer.getvalue(),
                        file_name="reiki_13hen_knowledge_base.zip",
                        mime="application/zip",
                    )
        except Exception as e:
            st.error(
                f"ZIPファイルの読み込み・処理中にエラーが発生しました: {e}"
            )

else:
    if password:
        st.error("パスワードが違います。")
    else:
        st.warning("左側のサイドバーからパスワードを入力してください。")
