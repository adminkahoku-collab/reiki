import io
import re
import time
import json
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


# --- Geminiによる複数例規の一括要約生成関数（バッチ処理） ---
def generate_batch_summaries_with_gemini(
    rules_batch: list, api_key: str, model_name: str = "gemini-3.8-flash"
) -> dict:
    """
    複数（30〜40件）の例規を1回のリクエストで一括要約し、JSON形式で返却する関数
    rules_batch: [{'title': title, 'content': content}, ...]
    """
    if not api_key:
        return {item["title"]: "・（APIキー未設定のため要約スキップ）" for item in rules_batch}

    # 一括処理用のプロンプト構築
    prompt = """
あなたは自治体職員向けの例規要約アシスタントです。
以下に複数の例規（タイトルと本文）を提示します。
それぞれの例規について、主要なポイントや対象者、重要な手続きを箇条書きで3行程度で簡潔に要約してください。

【出力条件】
- 出力は必ず以下のJSONオブジェクト形式のみとし、マークダウンのコードブロック(```json ... ```)を含めてください。
- 各例規の「タイトル」をキーとし、値として箇条書き（・）で3点以内にまとめた要約文字列を設定してください。
- 挨拶や前置き、解説などは一切出力しないでください。

【出力フォーマット例】
{
  "○○条例": "・要約1行目\\n・要約2行目\\n・要約3行目",
  "○○規則": "・要約1行目\\n・要約2行目"
}

【対象例規リスト】
"""
    for idx, item in enumerate(rules_batch, 1):
        trimmed = item["content"][:2000].strip()
        prompt += f"\n--- 例規{idx} ---\nタイトル: {item['title']}\n本文:\n{trimmed}\n"

    time.sleep(1.0) # API連続アクセス緩和
    client = genai.Client(api_key=api_key)
    max_retries = 3

    for attempt in range(max_retries):
        try:
            response = client.models.generate_content(
                model=model_name,
                contents=prompt,
                config={"response_mime_type": "application/json"}  # JSON出力モードの指定
            )
            # レスポンスのJSONパース
            result_json = json.loads(response.text.strip())
            return result_json
        except Exception as e:
            if attempt < max_retries - 1:
                time.sleep(4 * (attempt + 1))
                continue
            else:
                st.warning(f"一括要約生成でエラーが発生しました: {e}")
                # エラー時は空辞書を返却（スキップ用）
                return {}

# --- バッチ処理対応版：Markdown全体の要約挿入関数 ---
def process_markdown_summaries(
    md_text: str, api_key: str, progress_callback=None, batch_size: str = 35
) -> str:
    """Markdown全体を解析し、35件ごとに一括でGeminiへ要約リクエストを送信して埋め込む"""
    sections = md_text.split("\n\n---\n\n")
    parsed_sections = []
    rules_to_process = []

    # 1. 各セクションの構造解析
    for idx, sec in enumerate(sections):
        sec_str = sec.strip()
        if not sec_str:
            continue

        lines = sec_str.splitlines()
        rule_title = None
        title_line_idx = -1

        for i, line in enumerate(lines):
            if line.startswith("## "):
                rule_title = line.replace("## ", "").strip()
                title_line_idx = i
                break

        if rule_title and title_line_idx != -1:
            body_lines = lines[title_line_idx + 1 :]
            body_text = "\n".join(body_lines).strip()
            if body_text:
                parsed_sections.append({
                    "is_rule": True,
                    "title": rule_title,
                    "lines": lines,
                    "title_idx": title_line_idx,
                    "body": body_text
                })
                rules_to_process.append({
                    "title": rule_title,
                    "content": body_text
                })
            else:
                parsed_sections.append({"is_rule": False, "raw": sec_str})
        else:
            parsed_sections.append({"is_rule": False, "raw": sec_str})

    # 2. 35件ずつのバッチに分けて Gemini API を呼び出し
    summaries_dict = {}
    total_rules = len(rules_to_process)

    for i in range(0, total_rules, batch_size):
        batch = rules_to_process[i:i + batch_size]
        batch_results = generate_batch_summaries_with_gemini(batch, api_key)
        summaries_dict.update(batch_results)

        if progress_callback:
            progress_callback(min(i + batch_size, total_rules), total_rules)

    # 3. 生成された要約をMarkdown形式へ再構築
    processed_sections = []
    for item in parsed_sections:
        if not item["is_rule"]:
            processed_sections.append(item["raw"])
            continue

        title = item["title"]
        lines = item["lines"]
        t_idx = item["title_idx"]
        summary = summaries_dict.get(title, "・（要約生成スキップ）")

        summary_block = "> **【概要・要約】**\n" + "\n".join(
            [f"> {line}" for line in summary.splitlines() if line.strip()]
        )

        new_sec = (
            "\n".join(lines[: t_idx + 1])
            + "\n\n"
            + summary_block
            + "\n\n"
            + "\n".join(lines[t_idx + 1 :])
        )
        processed_sections.append(new_sec)

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
