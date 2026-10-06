import json
import os
import re
import time
from google import genai
import streamlit as st

# ==========================================
# 1. Gemini API 呼び出し関数（10件一括処理版）
# ==========================================


def summarize_chunk_with_gemini(
    rules_chunk: list[dict], api_key: str
) -> dict[str, str]:
  """10件程度の例規グループをまとめてGemini APIに送り、タイトルをキーとする要約辞書を取得する関数"""
  if not api_key:
    return {}

  # 10件分のタイトルと本文（先頭1500文字に制限してトークンを節約）をプロンプト用に整形
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
            model=model_name,
            contents=prompt,
        )

        res_text = response.text.strip()
        # MarkdownのJSONブロック装飾があれば除去
        res_text = re.sub(r"^```json\s*", "", res_text)
        res_text = re.sub(r"^```\s*", "", res_text)
        res_text = re.sub(r"\s*```$", "", res_text)

        # 4.5秒待機（無料枠制限: 15 RPM 対策）
        time.sleep(4.5)

        # JSONテキストを辞書型に変換して返却
        return json.loads(res_text)

      except Exception as e:
        err_str = str(e)
        # 503混雑エラーや429レート制限エラー時はリトライ
        if "503" in err_str or "429" in err_str or "UNAVAILABLE" in err_str:
          time.sleep(8 * (attempt + 1))
          continue
        # その他のエラーの場合はモデルを切り替える
        break

  return {}


# ==========================================
# 2. Streamlit UI & アプリケーションメイン処理
# ==========================================

st.set_page_config(
    page_title="例規集要約付与ツール", page_icon="📜", layout="wide"
)

st.title("📜 自治体例規集 自動要約付与ツール")
st.markdown(
    "13編のMarkdownファイルを取り込み、10件ずつの安全な一括処理でGemini"
    " APIを用いて要約を生成・追加します。"
)

# APIキーの取得（st.secrets または サイドバー入力）
gemini_api_key = st.secrets.get("GEMINI_API_KEY", "")
with st.sidebar:
  st.header("⚙️ 設定")
  if not gemini_api_key:
    gemini_api_key = st.text_input(
        "Gemini API Key を入力してください", type="password"
    )

  st.divider()
  st.markdown("### 📌 処理仕様")
  st.caption("・1編あたり10件ずつチャンク化してAPIへ送信")
  st.caption("・応答途切れ（出力上限）を回避")
  st.caption("・無料枠制限（15 RPM）を遵守したウェイト処理込み")

if not gemini_api_key:
  st.warning(
      "👈 サイドバーから Gemini API Key を設定してください（または .streamlit/secrets.toml"
      " に記述）。"
  )
  st.stop()

# セッション状態の保持
if "md_dict" not in st.session_state:
  st.session_state.md_dict = {}  # 元のMarkdownデータ { ファイル名: 本文 }
if "updated_md_dict" not in st.session_state:
  st.session_state.updated_md_dict = (
      {}
  )  # 要約付与後のMarkdownデータ { ファイル名: 本文 }

# --- ステップ1: ファイルアップロード ---
st.header("1. 例規Markdownファイル（全13編）の読み込み")
uploaded_files = st.file_uploader(
    "13編のMarkdown（.md）ファイルをまとめて選択・ドラッグ＆ドロップしてください",
    type=["md"],
    accept_multiple_files=True,
)

if uploaded_files:
  # ファイル名で並び替え（01_総務編.md などを順序通りに並べる）
  sorted_files = sorted(uploaded_files, key=lambda x: x.name)

  for uploaded_file in sorted_files:
    # UTF-8で読み込み
    string_data = uploaded_file.getvalue().decode("utf-8")
    st.session_state.md_dict[uploaded_file.name] = string_data

  st.success(
      f"合計 {len(st.session_state.md_dict)} 件のファイルを読み込みました。"
  )

# --- ステップ2: 処理対象の選択と実行 ---
if st.session_state.md_dict:
  st.divider()
  st.header("2. 要約付与処理の実行")

  # 処理モード選択
  run_mode = st.radio(
      "処理モードを選択してください:",
      ["全ファイル（全編）を一括処理する", "選択したファイルのみ処理する"],
      horizontal=True,
  )

  selected_keys = []
  if run_mode == "選択したファイルのみ処理する":
    selected_keys = st.multiselect(
        "処理対象の編ファイルを選択してください:",
        list(st.session_state.md_dict.keys()),
        default=list(st.session_state.md_dict.keys())[0:1],
    )
  else:
    selected_keys = list(st.session_state.md_dict.keys())

  if st.button("🤖 要約生成を開始する", type="primary"):
    progress_bar = st.progress(0)
    status_text = st.empty()

    total_files = len(selected_keys)

    for file_idx, file_name in enumerate(selected_keys):
      content = st.session_state.md_dict[file_name]

      # "## " で区切って各例規ブロックを分解
      rules_raw = content.split("## ")
      header = rules_raw[0]  # ファイル先頭のヘッダー情報

      # 構造化データ（タイトルと本文）の抽出
      parsed_rules = []
      for block in rules_raw[1:]:
        lines = block.split("\n")
        title = lines[0].strip()
        body = "\n".join(lines[1:])
        parsed_rules.append({"title": title, "body": body})

      total_rules_in_file = len(parsed_rules)
      all_summaries = {}

      # 10件ずつのグループ（チャンク）にしてAPI送信
      chunk_size = 10
      for i in range(0, total_rules_in_file, chunk_size):
        chunk = parsed_rules[i : i + chunk_size]
        end_idx = min(i + chunk_size, total_rules_in_file)

        status_text.info(
            f"📄 **[{file_idx+1}/{total_files}] {file_name}** を処理中..."
            f" ({i+1}〜{end_idx} / 全{total_rules_in_file}件)"
        )

        # Gemini APIを呼び出して10件まとめて要約を取得
        chunk_summaries = summarize_chunk_with_gemini(chunk, gemini_api_key)
        all_summaries.update(chunk_summaries)

      # 元のMarkdown構造へ要約を挿入して再組み上げ
      updated_content = header
      for r in parsed_rules:
        # API返却値からタイトルに一致する要約を取得（取得失敗時のフォールバック設定）
        summary_text = all_summaries.get(
            r["title"], "（要約生成エラー：一時的な混雑または出力不可）"
        )

        # Markdown引用記号（> ）を付与して整形
        formatted_summary = "> " + summary_text.replace("\n", "\n> ")

        updated_content += (
            f"## {r['title']}\n\n> **【概要・要約】**\n{formatted_summary}\n\n{r['body']}"
        )

      # 処理結果をセッション領域に保存
      st.session_state.updated_md_dict[file_name] = updated_content

      # プログレスバーの更新
      progress_bar.progress((file_idx + 1) / total_files)

    status_text.empty()
    st.success("🎉 指定したすべてのファイルの要約付与処理が完了しました！")

# --- ステップ3: 結果のプレビューとダウンロード ---
if st.session_state.updated_md_dict:
  st.divider()
  st.header("3. 処理結果の確認とダウンロード")

  # プレビュー表示用ファイルの選択
  preview_file = st.selectbox(
      "結果を表示するファイルを選択してください:",
      list(st.session_state.updated_md_dict.keys()),
  )

  if preview_file:
    st.download_button(
        label=f"📥 {preview_file} をダウンロード",
        data=st.session_state.updated_md_dict[preview_file],
        file_name=f"summary_{preview_file}",
        mime="text/markdown",
    )

    with st.expander("👁️ プレビューを表示（先頭2,000文字）"):
      st.text(st.session_state.updated_md_dict[preview_file][:2000] + "\n...")
