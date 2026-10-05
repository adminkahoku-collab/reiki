import io
import re
import time
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

# --- Geminiによる例規ごとの要約生成関数（リトライ＆ウェイト対応） ---
def generate_summary_with_gemini(
    title: str, content: str, api_key: str
) -> str:
  """Gemini を使用して例規ごとの要約を生成（レート制限対策付き）"""
  if not api_key:
    return "（APIキー未設定のため要約スキップ）"

  prompt = f"""
あなたは自治体職員向けの例規要約アシスタントです。
以下の例規について、概要やポイントを箇条書きで3行程度で簡潔に要約してください。

【出力条件】
- 箇条書き（・）で要点、対象者、重要な手続きなどを記載してください。
- タイトルや余計な挨拶は含めず、要約本文のみを出力してください。

【対象例規】
タイトル: {title}
本文:
{content[:2000]}
"""

  client = genai.Client(api_key=api_key)

  # 429エラーが発生した際のリトライ処理（最大3回まで試行）
  max_retries = 3
  for attempt in range(max_retries):
    try:
      response = client.models.generate_content(
          model="gemini-3.6-flash",
          contents=prompt,
      )

      # 無料枠の制限（1分5回）を考慮し、成功後も12秒間ウェイトを置く
      time.sleep(12)
      return response.text.strip()

    except Exception as e:
      if "429" in str(e) or "RESOURCE_EXHAUSTED" in str(e):
        if attempt < max_retries - 1:
          wait_time = 25  # エラーメッセージの指示通り25秒待機して再試行
          st.warning(
              f"API制限に達したため、{wait_time}秒待機して再試行します... ({title})"
          )
          time.sleep(wait_time)
          continue
      st.warning(f"Gemini API（要約生成）でエラー ({title}): {e}")
      return "（要約生成エラー）"

  return "（要約生成エラー：リトライ上限到達）"


# --- メイン処理 ---
if password == "reiki063215":
  st.success("認証されました。")

  if not gemini_api_key:
    st.info(
        "💡 .streamlit/secrets.toml に GEMINI_API_KEY"
        " が設定されていない場合、ステップ2の要約生成は簡易表示になります。"
    )

  # ==========================================
  # ステップ1: 元データ(ZIP)から13編Markdownを作成 (AIキーワード不要)
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

                    raw_text = j_soup.get_text(separator="\n", strip=True)
                    lines = [
                        line.strip()
                        for line in raw_text.splitlines()
                        if line.strip()
                    ]
                    title_pattern = re.compile(
                        rf"^(○)?{re.escape(rule_title)}$"
                    )
                    cleaned_lines = [
                        line for line in lines if not title_pattern.match(line)
                    ]
                    cleaned_text = "\n".join(cleaned_lines)

                    # キーワード生成を排除し、直接見出しと本文を追加
                    hen_markdown += (
                        f"## {rule_title}\n\n{cleaned_text}\n\n---\n\n"
                    )

                out_zip.writestr(
                    f"{hen_name}.md", hen_markdown.encode("utf-8")
                )

            st.success("🎉 ステップ1完了！13編のMarkdownを作成しました。")
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

      st.info(f"📁 ZIPから {len(md_dict)} 個の「編」ファイルを読み込みました。")

      # 処理対象の「編」を選択するUI
      st.subheader("🎯 処理対象のデータ（編）を指定")
      select_all = st.checkbox("すべての編を選択する", value=True)
      available_keys = list(md_dict.keys())
      selected_keys = st.multiselect(
          "要約を付与したい「編」を選択してください:",
          options=available_keys,
          default=available_keys if select_all else [],
      )

      if st.button("🤖 2. 選択した編の例規ごとに要約を付与する"):
        if not selected_keys:
          st.warning("処理対象の編が選択されていません。")
        else:
          progress_bar = st.progress(0)
          status_text = st.empty()

          updated_md_dict = md_dict.copy()

          for idx, key in enumerate(selected_keys):
            status_text.text(
                f"要約生成中 ({idx+1}/{len(selected_keys)}): {key}"
            )
            content = md_dict[key]

            # ## 見出しで分解して例規ごとに処理
            rules = content.split("## ")
            updated_content = rules[0]  # # 01_第1編... 等のヘッダー部分

            for rule_block in rules[1:]:
              lines = rule_block.split("\n")
              rule_title = lines[0].strip()
              rule_body = "\n".join(lines[1:])

              # 例規ごとの要約生成（Secretsから自動補給）
              summary = generate_summary_with_gemini(
                  rule_title, rule_body, gemini_api_key
              )

              # 例規タイトルの直下に【概要・要約】ブロックを挿入
              updated_content += (
                  f"## {rule_title}\n\n> **【概要・要約】**\n> "
                  + summary.replace("\n", "\n> ")
                  + f"\n\n{rule_body}"
              )

            updated_md_dict[key] = updated_content
            progress_bar.progress((idx + 1) / len(selected_keys))

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
      st.error(f"ZIPファイルの読み込み・処理中にエラーが発生しました: {e}")

else:
  if password:
    st.error("パスワードが違います。")
  else:
    st.warning("左側のサイドバーからパスワードを入力してください。")
