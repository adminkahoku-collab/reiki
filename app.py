import io
import re
import zipfile
from bs4 import BeautifulSoup
from google import genai
import requests
import streamlit as st

st.set_page_config(
    page_title="例規13編自動分割＆要約・Dify自動同期ツール",
    page_icon="⚖",
    layout="wide",
)
st.title("⚖️ 例規13編ファイル分割・要約付与・Dify一括更新ツール")

# --- 設定サイドバー ---
st.sidebar.header("🔑 API・認証設定")
password = st.sidebar.text_input("職員用パスワード", type="password")

st.sidebar.markdown("---")
st.sidebar.subheader("🤖 Gemini API 設定")
gemini_api_key = st.sidebar.text_input(
    "Gemini API Key",
    type="password",
    help="Google AI Studioで取得したAPIキーを入力してください",
)

st.sidebar.markdown("---")
st.sidebar.subheader("🚀 Dify Knowledge API 設定")
dify_base_url = st.sidebar.text_input("Dify URL", value="http://localhost/v1")
dify_api_key = st.sidebar.text_input("Dify Dataset API Key", type="password")


# --- Geminiによるキーワード自動生成関数 ---
def generate_keywords_with_gemini(
    title: str, content: str, api_key: str
) -> str:
  """Gemini を使用して例規のBM25検索補強用キーワードを自動生成"""
  if not api_key:
    return f"{title} 申請 手当"

  prompt = f"""
あなたは自治体例規集（RAGシステム）のインデックス作成アシスタントです。
以下の例規の「タイトル」と「本文」を読み、住民や職員が検索する際に使用しそうな「検索キーワード（単語）」を抽出・補完してください。

【出力条件】
1. 例規名（{title}）に直接含まれない同義語、類義語、関連する実務用語、略称を優先して抽出してください。
2. 「条例」「規則」「規程」「に関する」「について」などの一般的・形式的な言葉は除外してください。
3. 単語のみを「半角スペース区切り」で1行で出力してください。

【対象例規】
タイトル: {title}
本文冒頭: {content[:1000]}
"""
  try:
    client = genai.Client(api_key=api_key)
    response = client.models.generate_content(
        model="gemini-2.5-flash",
        contents=prompt,
    )
    keywords = response.text.strip()
    return keywords.replace("\n", " ").replace("、", " ").replace(",", " ")
  except Exception as e:
    st.warning(f"Gemini API（キーワード生成）でエラー ({title}): {e}")
    return f"{title} 手当 申請"


# --- Geminiによる要約生成関数 ---
def generate_summary_with_gemini(
    title: str, content: str, api_key: str
) -> str:
  """Gemini を使用して例規の要約・ポイントを生成"""
  if not api_key:
    return "要約（APIキー未設定のためスキップ）"

  prompt = f"""
あなたは自治体職員向けの例規要約アシスタントです。
以下の例規について、職員が素早く目的や要点を把握できるように箇条書きで3行程度で簡潔に要約してください。

【出力条件】
- 「要約:」などのタイトルヘッダーは不要です。
- 箇条書き（・）で要点、対象者、重要な手続きや基準を記載してください。

【対象例規】
タイトル: {title}
本文:
{content[:2000]}
"""
  try:
    client = genai.Client(api_key=api_key)
    response = client.models.generate_content(
        model="gemini-2.5-flash",
        contents=prompt,
    )
    return response.text.strip()
  except Exception as e:
    st.warning(f"Gemini API（要約生成）でエラー ({title}): {e}")
    return "要約生成エラー"


# --- メイン処理 ---
if password == "reiki063215":
  st.success("認証されました。")

  # タブで「新規HTML作成」と「途中再開・編集」を切り替え
  tab1, tab2 = st.tabs(
      ["📁 1. 元データ(HTML ZIP)から分割作成", "🔄 2. 分割済みZIPの読み込み・要約・処理"]
  )

  # セッション状態の初期化
  if "generated_md_dict" not in st.session_state:
    st.session_state["generated_md_dict"] = {}

  # ==========================================
  # タブ1: HTML ZIP から分割Markdownの作成
  # ==========================================
  with tab1:
    uploaded_html_zip = st.file_uploader(
        "例規HTMLデータのZIPファイルをアップロードしてください",
        type=["zip"],
        key="html_zip",
    )

    if uploaded_html_zip is not None:
      if st.button("⚖️ 例規マークダウン分割生成（AIキーワード付与）"):
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
                bunya_html = bunya_bytes.decode("utf-8", errors="ignore")

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
                      for line in elem.get_text("\n", strip=True).split("\n")
                      if line.strip()
                  ]
                  for line in lines:
                    hen_match = re_hen.search(line)
                    if hen_match:
                      clean_text = re.sub(
                          r'[\\/:*?"<>|]', "_", hen_match.group(0).strip()
                      )
                      if not current_hen.endswith(clean_text):
                        hen_counter += 1
                        current_hen = f"{hen_counter:02d}_{clean_text}"
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
                            d["id"] == doc_id for d in hen_data[current_hen]
                        ):
                          hen_data[current_hen].append(
                              {"id": doc_id, "title": title}
                          )

              if "00_未分類" in hen_data and not hen_data["00_未分類"]:
                del hen_data["00_未分類"]

              output_zip_buffer = io.BytesIO()
              generated_md_dict = {}

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
                  progress_bar.progress(current_hen_idx / total_hens)

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
                        j_html = j_bytes.decode("utf-8", errors="ignore")

                      j_soup = BeautifulSoup(j_html, "html.parser")
                      for tag in j_soup(["script", "style", "noscript"]):
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

                      keywords = generate_keywords_with_gemini(
                          rule_title, cleaned_text, gemini_api_key
                      )

                      hen_markdown += (
                          f"<!-- 検索キーワード: {keywords} -->\n"
                      )
                      hen_markdown += (
                          f"## {rule_title}\n\n{cleaned_text}\n\n---\n\n"
                      )

                  generated_md_dict[hen_name] = hen_markdown
                  out_zip.writestr(
                      f"{hen_name}.md", hen_markdown.encode("utf-8")
                  )

              st.session_state["generated_md_dict"] = generated_md_dict
              st.session_state["zip_bytes"] = output_zip_buffer.getvalue()

              st.success("🎉 分割処理完了！")
              st.download_button(
                  label="📥 13編分割済みMarkdown (ZIP) をダウンロード",
                  data=st.session_state["zip_bytes"],
                  file_name="reiki_13hen_markdowns.zip",
                  mime="application/zip",
              )
        except Exception as e:
          st.error(f"エラーが発生しました: {str(e)}")

  # ==========================================
  # タブ2: 分割済みZIPの読み込み・要約・個別処理
  # ==========================================
  with tab2:
    st.subheader("🔄 ダウンロード済みMarkdown (ZIP) をアップロードして再開")
    uploaded_md_zip = st.file_uploader(
        "以前ダウンロードしたMarkdown ZIPファイルを指定してください",
        type=["zip"],
        key="md_zip",
    )

    # 外部ファイルの読み込み処理
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

        st.session_state["generated_md_dict"] = md_dict
        st.info(f"💡 ZIPから {len(md_dict)} 件の編データを読み込みました。")
      except Exception as e:
        st.error(f"ZIPファイルの読み込みエラー: {e}")

    # 現在メモリ上（または読み込み済み）の編一覧を表示・選択
    current_data = st.session_state.get("generated_md_dict", {})

    if current_data:
      st.markdown("---")
      st.subheader("🎯 処理対象のデータ指定")

      # 全選択/全解除ボタン
      col1, col2 = st.columns([1, 4])
      select_all = col1.checkbox("すべて選択", value=True)

      available_keys = list(current_data.keys())
      selected_keys = st.multiselect(
          "処理を行う「編」を選択してください:",
          options=available_keys,
          default=available_keys if select_all else [],
      )

      st.markdown("---")
      st.subheader("📝 選択したデータへAI要約の付与")

      if st.button("🤖 選択した編に要約を追加する"):
        if not selected_keys:
          st.warning("処理対象の編が選択されていません。")
        else:
          progress_bar = st.progress(0)
          status_text = st.empty()

          for idx, key in enumerate(selected_keys):
            status_text.text(
                f"要約生成中 ({idx+1}/{len(selected_keys)}): {key}"
            )
            content = current_data[key]

            # ## で区切られた各例規ごとに分解して要約を挿入
            rules = content.split("## ")
            updated_content = rules[0]  # # 01_第1編... などのヘッダー部分

            for rule_block in rules[1:]:
              lines = rule_block.split("\n")
              rule_title = lines[0].strip()
              rule_body = "\n".join(lines[1:])

              # 要約をGeminiで生成
              summary = generate_summary_with_gemini(
                  rule_title, rule_body, gemini_api_key
              )

              # Markdown内に要約ブロックを追加
              updated_content += (
                  f"## {rule_title}\n\n> **【概要・要約】**\n> "
                  + summary.replace("\n", "\n> ")
                  + f"\n\n{rule_body}"
              )

            current_data[key] = updated_content
            progress_bar.progress((idx + 1) / len(selected_keys))

          st.session_state["generated_md_dict"] = current_data
          st.success("✨ 選択された編への要約付与が完了しました！")

      # 更新されたデータのダウンロード
      out_zip_buffer = io.BytesIO()
      with zipfile.ZipFile(
          out_zip_buffer, "w", zipfile.ZIP_DEFLATED
      ) as out_zip:
        for k, v in current_data.items():
          out_zip.writestr(f"{k}.md", v.encode("utf-8"))

      st.download_button(
          label="📥 更新済みMarkdown (ZIP) をダウンロード",
          data=out_zip_buffer.getvalue(),
          file_name="reiki_13hen_updated.zip",
          mime="application/zip",
      )
    else:
      st.info(
          "左側のタブで新規作成するか、既存のMarkdown ZIPファイルをアップロードしてください。"
      )

else:
  if password:
    st.error("パスワードが違います。")
  else:
    st.warning("左側のサイドバーからパスワードを入力してください。")
