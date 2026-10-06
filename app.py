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
  """bunya_00100000.html を起点に、中分類・小分類リンクを含めて13編の例規(H*****_J.html)を集約する"""
  md_dict = {}

  with zipfile.ZipFile(io.BytesIO(zip_file_bytes)) as z:
    file_map = {f.lower().replace("\\", "/"): f for f in z.namelist()}

    # 1. 目次ファイル (bunya_00100000.html) のパスを特定
    bunya_path = None
    for norm_f, raw_f in file_map.items():
      if os.path.basename(norm_f) == "bunya_00100000.html":
        bunya_path = raw_f
        break

    if not bunya_path:
      st.error(
          "⚠️ 目次ファイル (bunya_00100000.html) がZIP内に見つかりませんでした。"
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
  st.caption("2. 目次(bunya_00100000.html)に基づき13編の.mdを出力")
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
st.header("1. DVDデータ（ZIPファイル）のアップロード")

uploaded_zip = st.file_uploader(
    "DVDのZIPファイルをアップロードしてください", type=["zip"]
)

if uploaded_zip:
  if (
      "loaded_zip_name" not in st.session_state
      or st.session_state.loaded_zip_name != uploaded_zip.name
  ):
    with st.spinner(
        "bunya_00100000.html（目次）の階層構造を解析し、13編の例規データ(H*****_J.html)を集約中..."
    ):
      st.session_state.md_dict = extract_13_hens_from_zip(
          uploaded_zip.getvalue()
      )
      st.session_state.loaded_zip_name = uploaded_zip.name

  if st.session_state.md_dict:
    st.success(
        f"✅ 目次解析完了！ **合計 {len(st.session_state.md_dict)} 件の編（Markdownファイル）**"
        " に正しく分割・作成されました。"
    )

    # 13編の分割結果一覧
    with st.expander("📋 作成されたMarkdownファイル一覧を確認"):
      for fname in sorted(st.session_state.md_dict.keys()):
        rule_count = st.session_state.md_dict[fname].count("\n## ")
        st.write(f"- **{fname}**（収録例規数: 約 {rule_count} 件）")

    # 未要約Markdownの確認・ダウンロード
    st.markdown("#### 📥 変換された13編のMarkdownファイルをダウンロード・確認")

    col_raw1, col_raw2 = st.columns(2)
    selected_raw_file = st.selectbox(
        "確認・ダウンロードする編を選択:",
        sorted(list(st.session_state.md_dict.keys())),
        key="raw_select",
    )

    with col_raw1:
      if selected_raw_file:
        st.download_button(
            label=f"📄 {selected_raw_file} (未要約) をダウンロード",
            data=st.session_state.md_dict[selected_raw_file],
            file_name=selected_raw_file,
            mime="text/markdown",
        )

    with col_raw2:
      raw_zip_buffer = io.BytesIO()
      with zipfile.ZipFile(raw_zip_buffer, "w") as zf:
        for fname, fcontent in st.session_state.md_dict.items():
          zf.writestr(fname, fcontent)

      st.download_button(
          label="📦 全13編の未要約MarkdownをまとめてZIPダウンロード",
          data=raw_zip_buffer.getvalue(),
          file_name="unsummarized_13_hens.zip",
          mime="application/zip",
      )

    with st.expander("👁 選択中の編のプレビュー表示（先頭1,500文字）"):
      st.text(st.session_state.md_dict[selected_raw_file][:1500] + "\n...")

# --- ステップ2: 対象の編を選択してAI要約を生成 ---
if st.session_state.md_dict:
  st.divider()
  st.header("2. 編を選択して要約の生成を開始")

  run_mode = st.radio(
      "実行モード:",
      [
          "選択した編のみ要約する（推奨: タイムアウト防止）",
          "全編を一括で要約する",
      ],
      horizontal=True,
  )

  selected_keys = []
  if "選択した編" in run_mode:
    selected_keys = st.multiselect(
        "要約を生成する編を選択してください（1編ずつを推奨）:",
        sorted(list(st.session_state.md_dict.keys())),
        default=sorted(list(st.session_state.md_dict.keys()))[0:1],
    )
  else:
    selected_keys = sorted(list(st.session_state.md_dict.keys()))

  if st.button("🤖 選択した編の要約生成を開始する", type="primary"):
    if not selected_keys:
      st.warning("処理対象の編が選択されていません。")
    else:
      progress_bar = st.progress(0)
      status_text = st.empty()

      total_files = len(selected_keys)

      for file_idx, file_name in enumerate(selected_keys):
        content = st.session_state.md_dict[file_name]

        rules_raw = content.split("## ")
        header = rules_raw[0]

        parsed_rules = []
        for block in rules_raw[1:]:
          lines = block.split("\n")
          title = lines[0].strip()
          body = "\n".join(lines[1:])
          parsed_rules.append({"title": title, "body": body})

        total_rules_in_file = len(parsed_rules)
        all_summaries = {}

        chunk_size = 10
        for i in range(0, total_rules_in_file, chunk_size):
          chunk = parsed_rules[i : i + chunk_size]
          end_idx = min(i + chunk_size, total_rules_in_file)

          status_text.info(
              f"📄 **[{file_idx+1}/{total_files}] {file_name}** を処理中..."
              f" ({i+1}〜{end_idx} / 全{total_rules_in_file}件)"
          )

          chunk_summaries = summarize_chunk_with_gemini(chunk, gemini_api_key)
          all_summaries.update(chunk_summaries)

        # 要約組み込み
        updated_content = header
        for r in parsed_rules:
          summary_text = all_summaries.get(
              r["title"], "（要約生成エラー：一時的な混雑または出力不可）"
          )
          formatted_summary = "> " + summary_text.replace("\n", "\n> ")
          updated_content += (
              f"## {r['title']}\n\n> **【概要・要約】**\n{formatted_summary}\n\n{r['body']}"
          )

        st.session_state.updated_md_dict[file_name] = updated_content
        progress_bar.progress((file_idx + 1) / total_files)

      status_text.empty()
      st.success("🎉 選択した編の要約生成が完了しました！")

# --- ステップ3: 要約済みMarkdownのダウンロード ---
if st.session_state.updated_md_dict:
  st.divider()
  st.header("3. 要約付きMarkdownファイルのダウンロード")

  preview_file = st.selectbox(
      "要約完了ファイルを選択:",
      sorted(list(st.session_state.updated_md_dict.keys())),
  )

  col1, col2 = st.columns(2)

  with col1:
    if preview_file:
      st.download_button(
          label=f"📥 要約済み {preview_file} をダウンロード",
          data=st.session_state.updated_md_dict[preview_file],
          file_name=f"summary_{preview_file}",
          mime="text/markdown",
      )

  with col2:
    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, "w") as zf:
      for fname, fcontent in st.session_state.updated_md_dict.items():
        zf.writestr(fname, fcontent)

    st.download_button(
        label="📦 要約完了ファイルをまとめてZIPダウンロード",
        data=zip_buffer.getvalue(),
        file_name="summarized_13_hens.zip",
        mime="application/zip",
    )

  if preview_file:
    with st.expander("👁️ 要約済みプレビュー（先頭2,000文字）"):
      st.text(st.session_state.updated_md_dict[preview_file][:2000] + "\n...")
