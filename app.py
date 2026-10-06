import io
import json
import os
import re
import time
import zipfile
from bs4 import BeautifulSoup
from google import genai
import streamlit as st

# ==========================================
# 1. HTML -> Markdown 解析ユーティリティ
# ==========================================


def parse_reiki_html(html_content: str) -> tuple[str, str]:
  """例規本文HTML (H*****_J.html) からタイトルと本文を抽出"""
  soup = BeautifulSoup(html_content, "html.parser")

  # タイトル抽出
  title = ""
  title_tag = (
      soup.find("h1")
      or soup.find("div", class_="title")
      or soup.find("span", class_="title")
  )
  if title_tag:
    title = title_tag.get_text(strip=True)
  elif soup.title:
    title = soup.title.get_text(strip=True)
  else:
    title = "無題の例規"

  title = re.sub(r"[\r\n\t]", "", title)

  # 不要タグの削除
  for tag in soup(["script", "style", "nav", "header", "footer", "iframe"]):
    tag.decompose()

  # 本文抽出
  body_element = soup.find("div", id="honbun") or soup.find("body") or soup
  body_text = body_element.get_text()

  # テキスト整形
  body_text = re.sub(r"\r\n|\r", "\n", body_text)
  body_text = re.sub(r"\n{3,}", "\n\n", body_text).strip()

  return title, body_text


def resolve_path(base_path: str, href: str) -> str:
  """相対パスをZIP内の標準化パスに変換"""
  base_dir = os.path.dirname(base_path)
  joined = os.path.join(base_dir, href)
  return os.path.normpath(joined).replace("\\", "/")


def extract_13_hens_from_zip(zip_file_bytes) -> dict[str, str]:
  """bunya_0010000.html を起点に、中分類・小分類リンクを含めて13編の例規(H*****_J.html)を集約する"""
  md_dict = {}

  with zipfile.ZipFile(io.BytesIO(zip_file_bytes)) as z:
    file_map = {f.lower().replace("\\", "/"): f for f in z.namelist()}

    # 1. 目次ファイル (bunya_0010000.html) のパスを特定
    bunya_path = None
    for norm_f, raw_f in file_map.items():
      filename = os.path.basename(norm_f)
      # bunya_0010000.html または bunya_ から始まるトップ目次を検索
      if (
          filename == "bunya_0010000.html"
          or filename == "bunya_00100000.html"
          or filename.startswith("bunya_00")
      ):
        bunya_path = raw_f
        break

    if not bunya_path:
      st.error(
          "⚠️ 目次ファイル (bunya_0010000.html) がZIP内に見つかりませんでした。"
      )
      return {}

    # HTML読み込みヘルパー
    def read_html_soup(zip_path: str):
      try:
        data = z.read(zip_path)
        try:
          html_str = data.decode("cp932")
        except UnicodeDecodeError:
          html_str = data.decode("utf-8", errors="ignore")
        return BeautifulSoup(html_str, "html.parser")
      except Exception:
        return None

    # 2. トップ目次から13編のルート領域と中分類リンクを取得
    soup_top = read_html_soup(bunya_path)
    if not soup_top:
      return {}

    hen_structure = {}  # { "第01編_〇〇": ["H1234_J.html のフルパス", ...], ... }
    current_hen = "第01編_未分類"

    # 目次内のすべての <a> タグを順に走査
    for a in soup_top.find_all("a"):
      text = a.get_text(strip=True)
      href = a.get("href", "")
      if not href or href.startswith("#") or href.startswith("javascript:"):
        continue

      # 編の切り替わり判定（"第1編", "第01編", "第１編" 等）
      hen_match = re.search(
          r"(第\s*[0-9０-９1-13]{1,2}\s*編[^\s＜＜＜<]*)"
          , text
      )
      if hen_match:
        current_hen = hen_match.group(1).replace(" ", "")
        if current_hen not in hen_structure:
          hen_structure[current_hen] = []

      # リンク先が例規本文(H*****_J.html)または中分類(bunya_*.html等)の場合
      target_rel_path = resolve_path(bunya_path, href)
      target_key = target_rel_path.lower()

      if target_key in file_map:
        real_target_path = file_map[target_key]

        # 例規本文HTMLの場合
        if re.search(r"H\d+.*_J\.html?$", real_target_path, re.IGNORECASE):
          if current_hen not in hen_structure:
            hen_structure[current_hen] = []
          if real_target_path not in hen_structure[current_hen]:
            hen_structure[current_hen].append(real_target_path)

        # 中分類・小分類HTMLの場合（再帰的に辿ってH*****_J.htmlを回収）
        elif real_target_path.endswith(".html") or real_target_path.endswith(
            ".htm"
        ):
          soup_sub = read_html_soup(real_target_path)
          if soup_sub:
            for sub_a in soup_sub.find_all("a"):
              sub_href = sub_a.get("href", "")
              if not sub_href:
                continue
              h_rel_path = resolve_path(real_target_path, sub_href)
              h_key = h_rel_path.lower()

              if (
                  h_key in file_map
                  and re.search(
                      r"H\d+.*_J\.html?$", file_map[h_key], re.IGNORECASE
                  )
              ):
                if current_hen not in hen_structure:
                  hen_structure[current_hen] = []
                if file_map[h_key] not in hen_structure[current_hen]:
                  hen_structure[current_hen].append(file_map[h_key])

    # 3. 各編のMarkdownファイルを構築
    for idx, (hen_name, html_paths) in enumerate(
        sorted(hen_structure.items()), 1
    ):
      if not html_paths:
        continue

      # ディレクトリ名・ファイル名用の正規化
      clean_name = re.sub(r"^第\d+編", "", hen_name).strip("_ ")
      formatted_hen_name = (
          f"第{idx:02d}編_{clean_name}" if clean_name else f"第{idx:02d}編"
      )

      md_content = f"# {formatted_hen_name}\n\n"

      for real_path in html_paths:
        file_bytes = z.read(real_path)
        try:
          html_str = file_bytes.decode("cp932")
        except UnicodeDecodeError:
          html_str = file_bytes.decode("utf-8", errors="ignore")

        title, body = parse_reiki_html(html_str)
        md_content += f"## {title}\n\n{body}\n\n---\n\n"

      md_dict[f"{formatted_hen_name}.md"] = md_content

  return md_dict


# ==========================================
# 2. Gemini API 呼び出し関数
# ==========================================


def summarize_chunk_with_gemini(
    rules_chunk: list[dict], api_key: str
) -> dict[str, str]:
  """例規グループをまとめてGemini APIで要約"""
  if not api_key:
    return {}

  rules_text = ""
  for idx, r in enumerate(rules_chunk, 1):
    body_truncated = r["body"][:1500]
    rules_text += (
        f"--- 例規{idx} ---\nタイトル: {r['title']}\n本文:\n{body_truncated}\n\n"
    )

  prompt = f"""
あなたは自治体職員向けの例規要約アシスタントです。
以下の{len(rules_chunk)}件の例規について、それぞれの概要・要点を箇条書きで3行程度（100文字前後）で簡潔に要約してください。

【出力条件】
- 必ず指定されたJSONフォーマットのみを出力してください（説明文や ```json などの装飾タグは一切不要です）。
- キーは正確な「例規タイトル」、値は「箇条書きの要約テキスト」にしてください。

【出力フォーマット例】
{{
  "例規タイトル1": "・要点1\\n・要点2\\n・要点3",
  "例規タイトル2": "・要点1\\n・要点2"
}}

【対象例規データ】
{rules_text}
"""

  client = genai.Client(api_key=api_key)
  candidate_models = [
      "gemini-2.5-flash",
      "gemini-2.0-flash",
      "gemini-1.5-flash",
  ]

  for model_name in candidate_models:
    for attempt in range(3):
      try:
        response = client.models.generate_content(
            model=model_name, contents=prompt
        )

        res_text = response.text.strip()
        res_text = re.sub(r"^```json\s*", "", res_text)
        res_text = re.sub(r"^```\s*", "", res_text)
        res_text = re.sub(r"\s*```$", "", res_text)

        time.sleep(4.5)
        return json.loads(res_text)

      except Exception as e:
        err_str = str(e)
        if "503" in err_str or "429" in err_str or "UNAVAILABLE" in err_str:
          time.sleep(8 * (attempt + 1))
          continue
        break

  return {}


# ==========================================
# 3. Streamlit メインUI
# ==========================================

st.set_page_config(
    page_title="例規集 自動変換＆要約システム",
    page_icon="📜",
    layout="wide",
)

st.title("📜 自治体例規集 自動変換＆要約システム")

# APIキーの取得
gemini_api_key = st.secrets.get("GEMINI_API_KEY", "")

with st.sidebar:
  st.header("⚙️ API設定")
  if not gemini_api_key:
    gemini_api_key = st.text_input(
        "Gemini API Key を入力してください", type="password"
    )

  st.divider()
  st.markdown("### 📌 処理手順")
  st.caption("1. DVDのZIPをアップロード")
  st.caption("2. 目次(bunya_0010000.html)に基づき13編の.mdを出力")
  st.caption("3. 各編にAI要約を付与")

if not gemini_api_key:
  st.warning("👈 サイドバーから Gemini API Key を設定してください。")
  st.stop()

# セッション状態
if "md_dict" not in st.session_state:
  st.session_state.md_dict = {}
if "updated_md_dict" not in st.session_state:
  st.session_state.updated_md_dict = {}

# --- ステップ1: DVD(ZIP)のアップロードと13編Markdown化 ---
st.header("1. DVDデータ（ZIPファイル）の
