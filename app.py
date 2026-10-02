import streamlit as st
import zipfile
import io
import re
import time  # 先頭に追加
from bs4 import BeautifulSoup
from google import genai

st.set_page_config(page_title="例規13編自動分割＆AIキーワード付与ツール", page_icon="⚖", layout="wide")
st.title("⚖️ 例規13編ファイル自動分割・AIキーワード付与ツール")

# --- Streamlit Secrets から Gemini API キーを取得 ---
gemini_api_key = st.secrets.get("GEMINI_API_KEY", "")

# --- Gemini による検索キーワード自動生成関数 ---
def generate_keywords_with_gemini(
    title: str, content: str, api_key: str
) -> str:
    """Gemini 3.6 Flash を使用して、例規の検索用キーワードを自動生成する関数（429エラー自動待機付き）"""
    if not api_key:
        return f"{title}"

    prompt = f"""
あなたは自治体例規集（RAGシステム）のインデックス作成アシスタントです。
以下の例規の「タイトル」と「本文」を読み、住民や職員が検索する際に使用しそうな「検索キーワード（単語）」を抽出・補完してください。

【出力条件】
1. 例規名（{title}）に直接含まれない同義語、類義語、関連する実務用語、略称を優先して抽出してください。
2. 「条例」「規則」「規程」「に関する」「について」などの一般的・形式的な言葉は除外してください。
3. 単語のみを「半角スペース区切り」で1行で出力してください（説明文や余計な記号は一切含めないでください）。

【対象例規】
タイトル: {title}
本文冒頭: {content[:1000]}
"""

    client = genai.Client(api_key=api_key)

    # 429エラー発生時に自動で再試行するループ
    max_retries = 5
    for attempt in range(max_retries):
        try:
            response = client.models.generate_content(
                model="gemini-3.6-flash", contents=prompt
            )

            keywords = response.text.strip()
            # クレンジング処理
            keywords = (
                keywords.replace("\n", " ")
                .replace("、", " ")
                .replace(",", " ")
                .replace("<!--", "")
                .replace("-->", "")
            )
            return keywords

        except Exception as e:
            error_str = str(e)
            if "429" in error_str or "RESOURCE_EXHAUSTED" in error_str:
                # レート制限エラーの場合は15秒待ってリトライ
                time.sleep(15)
            else:
                st.warning(f"Gemini APIエラー ({title}): {e}")
                break

    # 最終的にリトライオーバーした場合のフォールバック
    return f"{title}"

# --- メイン画面処理 ---
# APIキー取得の事前チェック
if not gemini_api_key:
    st.error("⚠️️ Streamlit Cloud の Secrets に 'GEMINI_API_KEY' が設定されていません。Settings > Secrets を確認してください。")

uploaded_zip = st.file_uploader("例規データのZIPファイルをアップロードしてください", type=["zip"])

if uploaded_zip is not None:
    try:
        zip_buffer = io.BytesIO(uploaded_zip.read())
        
        with zipfile.ZipFile(zip_buffer, "r") as z:
            all_files = z.namelist()
            
            # 分類HTMLファイルと _J.html（本文）の抽出
            target_bunya = next((f for f in all_files if f.split("/")[-1].lower() in ["bunya_0010000.html", "bunya0010000.html"]), None)
            j_files = {f.split("/")[-1].replace("_J.html", "").replace("_j.html", ""): f for f in all_files if f.lower().endswith("_j.html")}

            if target_bunya:
                if st.button("⚖️ AIキーワード付与＆Markdown生成を実行"):
                    bunya_bytes = z.read(target_bunya)
                    try:
                        bunya_html = bunya_bytes.decode("cp932")
                    except UnicodeDecodeError:
                        bunya_html = bunya_bytes.decode("utf-8", errors="ignore")

                    bunya_soup = BeautifulSoup(bunya_html, "html.parser")
                    re_link = re.compile(r"OpenResDataWin\('([^']+)'\)")
                    re_hen = re.compile(r"第\s*[0-9０-９一二三四五六七八九十]+\s*編\s*.*")

                    hen_data = {}
                    current_hen = "00_未分類"
                    hen_counter = 0

                    # 分類HTMLから各編ごとの例規タイトル・IDをパース
                    for elem in bunya_soup.find_all(['strong', 'tr', 'p', 'div']):
                        if elem.name == 'strong':
                            lines = [line.strip() for line in elem.get_text("\n", strip=True).split("\n") if line.strip()]
                            for line in lines:
                                hen_match = re_hen.search(line)
                                if hen_match:
                                    matched_text = hen_match.group(0).strip()
                                    clean_text = re.sub(r'[\\/:*?"<>|]', '_', matched_text)
                                    if not current_hen.endswith(clean_text):
                                        hen_counter += 1
                                        current_hen = f"{hen_counter:02d}_{clean_text}"
                                        if current_hen not in hen_data:
                                            hen_data[current_hen] = []

                        elif elem.name == 'tr':
                            for a_tag in elem.find_all('a'):
                                onclick_attr = a_tag.get('href', '') or a_tag.get('onclick', '')
                                match = re_link.search(onclick_attr)
                                if match:
                                    doc_id = match.group(1)
                                    title = a_tag.get_text(strip=True)
                                    if title and doc_id:
                                        if current_hen not in hen_data:
                                            hen_data[current_hen] = []
                                        if not any(d['id'] == doc_id for d in hen_data[current_hen]):
                                            hen_data[current_hen].append({"id": doc_id, "title": title})

                    if "00_未分類" in hen_data and len(hen_data["00_未分類"]) == 0:
                        del hen_data["00_未分類"]

                    # Markdown生成処理
                    output_zip_buffer = io.BytesIO()
                    progress_bar = st.progress(0)
                    status_text = st.empty()
                    
                    total_hens = len(hen_data)
                    current_hen_idx = 0
                    total_docs = 0

                    with zipfile.ZipFile(output_zip_buffer, "w", zipfile.ZIP_DEFLATED) as out_zip:
                        for hen_name, items in hen_data.items():
                            current_hen_idx += 1
                            status_text.text(f"処理中 ({current_hen_idx}/{total_hens}): {hen_name} 内の例規をGemini解析中...")
                            progress_bar.progress(current_hen_idx / total_hens)
                            
                            if not items:
                                continue
                            
                            hen_markdown = f"# {hen_name}\n\n"
                            
                            for item in items:
                                doc_id = item['id']
                                rule_title = item['title']
                                
                                if doc_id in j_files:
                                    j_bytes = z.read(j_files[doc_id])
                                    try:
                                        j_html = j_bytes.decode("cp932")
                                    except UnicodeDecodeError:
                                        j_html = j_bytes.decode("utf-8", errors="ignore")

                                    j_soup = BeautifulSoup(j_html, "html.parser")
                                    for tag in j_soup(['script', 'style', 'noscript']):
                                        tag.decompose()

                                    raw_text = j_soup.get_text(separator="\n", strip=True)
                                    lines = [line.strip() for line in raw_text.splitlines() if line.strip()]

                                    cleaned_lines = []
                                    title_pattern = re.compile(rf"^(○)?{re.escape(rule_title)}$")
                                    for line in lines:
                                        if not title_pattern.match(line):
                                            cleaned_lines.append(line)

                                    cleaned_text = "\n".join(cleaned_lines)
                                    
                                    # 【Gemini API によるキーワード生成】
                                    keywords = generate_keywords_with_gemini(rule_title, cleaned_text, gemini_api_key)
                                    
                                    # Markdown構造の組み立て（冒頭に検索キーワードコメントを挿入）
                                    hen_markdown += f"<!-- 検索キーワード: {keywords} -->\n"
                                    hen_markdown += f"## {rule_title}\n\n{cleaned_text}\n\n---\n\n"
                                    total_docs += 1
                            
                            # 各編ごとの .md ファイルをZIPに書き込み
                            file_filename = f"{hen_name}.md"
                            out_zip.writestr(file_filename, hen_markdown.encode("utf-8"))

                    status_text.text("処理が完了しました！")
                    st.success(f"🎉 合計 {total_hens} つの分類（{total_docs} 件の例規）のAIキーワード付きMarkdown生成が完了しました。")

                    # ZIPダウンロードボタンを表示
                    st.download_button(
                        label="📥 13編分割済みMarkdown (ZIP) をダウンロード",
                        data=output_zip_buffer.getvalue(),
                        file_name="reiki_13hen_markdowns_ai.zip",
                        mime="application/zip"
                    )

            else:
                st.error("ZIP内に分類ファイル (bunya_0010000.html 等) が見つかりませんでした。")

    except Exception as e:
        st.error(f"エラーが発生しました: {str(e)}")
