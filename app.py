import io
import re
import time
import zipfile
from bs4 import BeautifulSoup
from google import genai
import streamlit as st
import json
import urllib.request

st.set_page_config(
    page_title="例規13編自動分割＆AIキーワード付与ツール",
    page_icon="⚖",
    layout="wide",
)
st.title("⚖️ 例規ファイル自動分割・AIキーワード付与ツール")

gemini_api_key = st.secrets.get("GEMINI_API_KEY", "")


# --- Gemini API によるキーワード生成関数 ---
def generate_keywords_with_gemini(
    title: str, content: str, api_key: str
) -> str:
    if not api_key:
        return f"{title}"

    prompt = f"""
あなたは自治体例規集のインデックス作成アシスタントです。
以下の例規の「タイトル」と「本文」を読み、検索用の補完キーワード（単語）を抽出してください。

【出力条件】
1. 例規名（{title}）に含まれない同義語、類義語、実務用語を優先抽出してください。
2. 「条例」「規則」等の形式単語は除外してください。
3. 半角スペース区切りの単語列のみを1行で出力してください。

【対象例規】
タイトル: {title}
本文冒頭: {content[:1000]}
"""
    client = genai.Client(api_key=api_key)

    for attempt in range(3):
        try:
            response = client.models.generate_content(
                model="gemini-3.6-flash", contents=prompt
            )
            keywords = response.text.strip()
            keywords = (
                keywords.replace("\n", " ")
                .replace("、", " ")
                .replace(",", " ")
                .replace("<!--", "")
                .replace("-->", "")
            )
            return keywords
        except Exception as e:
            if "429" in str(e) or "RESOURCE_EXHAUSTED" in str(e):
                time.sleep(15)
            else:
                break
    return f"{title}"

# --- Ollama (qwen2.5:7b) によるキーワード生成関数 ---
def generate_keywords_with_ollama(
    title: str, content: str, model_name: str = "qwen2.5:7b"
) -> str:
    # ★ Pythonプロセス全体でローカル通信のプロキシ利用を無効化（追加）
    os.environ["NO_PROXY"] = "127.0.0.1,localhost"
    os.environ["no_proxy"] = "127.0.0.1,localhost"
    prompt = f"""
あなたは自治体例規集のインデックス作成アシスタントです。
以下の例規の「タイトル」と「本文」を読み、検索用の補完キーワード（単語）を抽出してください。

【厳格な出力条件】
1. 例規名（{title}）に含まれていない同義語、類義語、関連する実務用語・対象分野のみを抽出してください。
2. 例規名そのもの（「{title}」）や、「条例」「規則」「規程」「に関する」などの形式単語は【絶対に含めないでください】。
3. 半角スペース区切りの単語列のみを1行で出力してください。
4. 例: 「休日 執務時間 閉庁日 年末年始」

【対象例規】
タイトル: {title}
本文冒頭: {content[:1000]}
"""

    url = "http://127.0.0.1:11434/api/generate"
    payload = {
        "model": model_name,
        "prompt": prompt,
        "stream": False,
    }

    try:
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        # CPU推論用にタイムアウトを180秒に設定
        with urllib.request.urlopen(req, timeout=180) as res:
            result = json.loads(res.read().decode("utf-8"))
            keywords = result.get("response", "").strip()

            keywords = (
                keywords.replace("\n", " ")
                .replace("、", " ")
                .replace(",", " ")
                .replace("<!--", "")
                .replace("-->", "")
            )
            return keywords
    except Exception as e:
        st.error(f"Ollama処理エラー ({title}): {e}")
        return ""

# =========================================================
# 【ステップ1】HTMLから全13編のベースMarkdown(ZIP)を一括作成
# =========================================================
st.header("1️⃣ ステップ1: ベースMarkdown（全13編ZIP）の作成")
st.caption(
    "元データのZIPをアップロードし、テキスト抽出済みのベースMarkdownを作成・ダウンロードします（AI不使用・数秒で完了）。"
)

uploaded_html_zip = st.file_uploader(
    "元データのZIPファイルをアップロードしてください",
    type=["zip"],
    key="step1_uploader",
)

if uploaded_html_zip is not None:
    if st.button("🚀 ベースMarkdown (全13編ZIP) を生成"):
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
                    bunya_html = bunya_bytes.decode("utf-8", errors="ignore")

                bunya_soup = BeautifulSoup(bunya_html, "html.parser")
                re_link = re.compile(r"OpenResDataWin\('([^']+)'\)")
                re_hen = re.compile(
                    r"第\s*[0-9０-９一二三四五六七八九十]+\s*編\s*.*"
                )

                hen_data = {}
                current_hen = "00_未分類"
                hen_counter = 0

                for elem in bunya_soup.find_all(["strong", "tr", "p", "div"]):
                    if elem.name == "strong":
                        lines = [
                            line.strip()
                            for line in elem.get_text("\n", strip=True).split(
                                "\n"
                            )
                            if line.strip()
                        ]
                        for line in lines:
                            hen_match = re_hen.search(line)
                            if hen_match:
                                matched_text = hen_match.group(0).strip()
                                clean_text = re.sub(
                                    r'[\\/:*?"<>|]', "_", matched_text
                                )
                                if not current_hen.endswith(clean_text):
                                    hen_counter += 1
                                    current_hen = (
                                        f"{hen_counter:02d}_{clean_text}"
                                    )
                                    if current_hen not in hen_data:
                                        hen_data[current_hen] = []
                    elif elem.name == "tr":
                        for a_tag in elem.find_all("a"):
                            onclick_attr = a_tag.get("href", "") or a_tag.get(
                                "onclick", ""
                            )
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
                                            {"id": doc_id, "title": title}
                                        )

                if "00_未分類" in hen_data and len(hen_data["00_未分類"]) == 0:
                    del hen_data["00_未分類"]

                # ZIPファイル書き込み
                base_zip_buffer = io.BytesIO()
                with zipfile.ZipFile(
                    base_zip_buffer, "w", zipfile.ZIP_DEFLATED
                ) as out_zip:
                    for hen_name, items in hen_data.items():
                        if not items:
                            continue
                        hen_markdown = f"# {hen_name}\n\n"
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

                                j_soup = BeautifulSoup(j_html, "html.parser")
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

                                cleaned_lines = []
                                title_pattern = re.compile(
                                    rf"^(○)?{re.escape(rule_title)}$"
                                )
                                for line in lines:
                                    if not title_pattern.match(line):
                                        cleaned_lines.append(line)

                                cleaned_text = "\n".join(cleaned_lines)
                                hen_markdown += f"## {rule_title}\n\n{cleaned_text}\n\n---\n\n"

                        out_zip.writestr(
                            f"{hen_name}.md", hen_markdown.encode("utf-8")
                        )

                st.success("⚡ 全13編のベースMarkdown（ZIP）が生成されました！")
                st.download_button(
                    label="📥 ベースMarkdown (13編ZIP) をダウンロード",
                    data=base_zip_buffer.getvalue(),
                    file_name="reiki_13hen_base_markdowns.zip",
                    mime="application/zip",
                )

st.markdown("---")

# =========================================================
# 【ステップ2】ステップ1のZIPを読み込み、1編ずつAI処理
# =========================================================
st.header("2️⃣ ステップ2: ベースZIPを読み込んでAIキーワード付与")
st.caption(
    "ステップ1でダウンロードした `reiki_13hen_base_markdowns.zip` をアップロードし、指定した編にAIキーワードを付与します。"
)

uploaded_base_zip = st.file_uploader(
    "ステップ1で作成したベースZIPをアップロードしてください",
    type=["zip"],
    key="step2_uploader",
)

if uploaded_base_zip is not None:
    base_zip_buffer = io.BytesIO(uploaded_base_zip.read())

    with zipfile.ZipFile(base_zip_buffer, "r") as z:
        md_files = [f for f in z.namelist() if f.endswith(".md")]

        if md_files:
            selected_md_file = st.selectbox(
                "AIキーワードを処理・付与したい「編（ファイル）」を選択してください",
                md_files,
            )

            # --- ステップ2の AIキーワード付与処理 ---
            if st.button(
                f"🤖 「{selected_md_file}」にAIキーワードを付与して保存"
            ):
                content = z.read(selected_md_file).decode("utf-8")

                # `---` (水平線) で各例規のブロックごとに分割する
                blocks = content.split("\n---\n")

                ai_enhanced_markdown = ""
                progress_bar = st.progress(0)
                status_text = st.empty()
                total_blocks = len(blocks)

                for idx, block in enumerate(blocks):
                    block_str = block.strip()
                    if not block_str:
                        continue

                    progress_bar.progress((idx + 1) / total_blocks)

                    # 最初（ファイルヘッダー # 01_第１編...）の処理
                    if block_str.startswith("# "):
                        ai_enhanced_markdown += block_str + "\n\n---\n\n"
                        continue

                    # ## 見出しからタイトルと本文を正しく抽出
                    match = re.search(
                        r"^##\s*(.*?)\n(.*)", block_str, re.DOTALL
                    )
                    if match:
                        rule_title = match.group(1).strip()
                        rule_content = match.group(2).strip()

                        status_text.text(
                            f"AI解析中 ({idx + 1}/{total_blocks}): {rule_title}"
                        )

                        # タイトルが存在する場合は Gemini に投げる
                        if rule_title:
                            #keywords = generate_keywords_with_gemini(
                            #    rule_title, rule_content, gemini_api_key
                            #)
                            #time.sleep(12)  # 無料枠制限（5 RPM）回
                            keywords = generate_keywords_with_ollama(
                                rule_title, rule_content, model_name="qwen2.5:7b"
                            )
                        
                        else:
                            keywords = rule_title

                        # 正しい構造（キーワード ➔ ## タイトル ➔ 本文）で組み立て
                        ai_enhanced_markdown += (
                            f"<!-- 検索キーワード: {keywords} -->\n"
                        )
                        ai_enhanced_markdown += (
                            f"## {rule_title}\n{rule_content}\n\n---\n\n"
                        )
                    else:
                        # マッチしない場合はそのまま保持
                        ai_enhanced_markdown += block_str + "\n\n---\n\n"

                st.success(
                    f"🎉 「{selected_md_file}」のAIキーワード付与が完了しました！"
                )
                st.download_button(
                    label=f"📥 {selected_md_file} (完成版) をダウンロード",
                    data=ai_enhanced_markdown.encode("utf-8"),
                    file_name=selected_md_file,
                    mime="text/markdown",
                )
