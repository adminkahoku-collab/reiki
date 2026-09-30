import streamlit as st
import zipfile
import io
import os
import re
from bs4 import BeautifulSoup

# 画面基本設定
st.set_page_config(page_title="例規データRAG変換ツール", page_icon="⚖️")
st.title("⚖️ 例規HTMLデータ ➔ RAGテキスト変換ツール")
st.write("DVDから抽出したZipファイルをアップロードすると、13編のRAG用テキストを生成して一括ダウンロードできます。")

# 簡易パスワード認証
password = st.text_input("職員用パスワードを入力してください", type="password")

if password == "reiki063215": # ← 必要に応じて変更してください
    st.success("認証されました。")
    st.markdown("---")
    
# 1. ZIPファイルのアップロード
uploaded_zip = st.file_uploader("例規データのZIPファイルをアップロードしてください", type=["zip"])

if uploaded_zip is not None:
    zip_buffer = io.BytesIO(uploaded_zip.read())
    
    with zipfile.ZipFile(zip_buffer, "r") as z:
        all_files = z.namelist()
        
        # --- 【ファイル特定の厳格化】 ---
        # bunya0010000.html や bunya_0010000.html などの特定ファイル名のみを検索
        target_bunya_file = None
        for f in all_files:
            filename = f.split("/")[-1].lower() # フォルダ階層を無視してファイル名のみで判定
            if filename in ["bunya_0010000.html", "bunya0010000.html"]:
                target_bunya_file = f
                break

        # 本文ファイル（_J.html）のリストアップ
        j_files = [f for f in all_files if f.endswith("_J.html") or f.endswith("_j.html")]

        # 情報表示
        st.subheader("📊 ZIP内の解析対象判定")
        col1, col2 = st.columns(2)
        
        if target_bunya_file:
            col1.success(f"目次ファイル検出: `{target_bunya_file}`")
        else:
            col1.error("❌ `bunya_0010000.html` が見つかりませんでした")

        col2.info(f"本文HTML (_J) 総数: {len(j_files)} 件")

        # 目次ファイルが見つかった場合のみ処理を実行
        if target_bunya_file and j_files:
            if st.button("絞り込みデータ化を実行する"):
                
                # --- A. 目次ファイル (bunya_0010000.html) の解析 ---
                bunya_bytes = z.read(target_bunya_file)
                try:
                    bunya_html = bunya_bytes.decode("cp932")
                except UnicodeDecodeError:
                    bunya_html = bunya_bytes.decode("utf-8", errors="ignore")

                bunya_soup = BeautifulSoup(bunya_html, "html.parser")
                re_link = re.compile(r"OpenResDataWin\('([^']+)'\)")
                
                categories = []
                # 目次内のリンク要素を抽出
                for elem in bunya_soup.find_all(['td', 'div', 'p', 'a']):
                    onclick_attr = elem.get('onclick', '') or elem.get('href', '')
                    match = re_link.search(onclick_attr)
                    if match:
                        doc_id = match.group(1)
                        title = elem.get_text(strip=True)
                        if title and doc_id:
                            categories.append({"id": doc_id, "title": title})

                # 重複IDの除去
                seen = set()
                unique_categories = []
                for item in categories:
                    if item['id'] not in seen:
                        seen.add(item['id'])
                        unique_categories.append(item)

                st.write(f"✅ `bunya_0010000.html` から **{len(unique_categories)} 件** の例規IDを取得しました。")

                # --- B. 該当する _J.html のみを抽出・パース ---
                # 高速参照用に _J ファイルのマップを作成
                j_file_map = {}
                for path in j_files:
                    filename = path.split("/")[-1]
                    doc_id = filename.replace("_J.html", "").replace("_j.html", "")
                    j_file_map[doc_id] = path

                processed_data = []
                missing_ids = []

                progress_bar = st.progress(0)

                for idx, item in enumerate(unique_categories):
                    doc_id = item['id']
                    
                    if doc_id in j_file_map:
                        # 対象の _J.html だけを読み込み
                        j_bytes = z.read(j_file_map[doc_id])
                        try:
                            j_html = j_bytes.decode("cp932")
                        except UnicodeDecodeError:
                            j_html = j_bytes.decode("utf-8", errors="ignore")

                        j_soup = BeautifulSoup(j_html, "html.parser")
                        
                        # 不要なタグ（スクリプトやスタイル）を除去
                        for tag in j_soup(['script', 'style', 'noscript']):
                            tag.decompose()

                        # テキスト（条本文）のみ抽出
                        full_text = j_soup.get_text(separator="\n", strip=True)

                        processed_data.append({
                            "id": doc_id,
                            "title": item['title'],
                            "text": full_text
                        })
                    else:
                        missing_ids.append(doc_id)

                    progress_bar.progress((idx + 1) / len(unique_categories))

                st.success(f"🎉 処理完了! 成功: **{len(processed_data)} 件** / 未検出: **{len(missing_ids)} 件**")

                if missing_ids:
                    with st.expander("`_J.html` が存在しなかったID一覧"):
                        st.write(missing_ids)

                # --- C. 結果のプレビューとダウンロード ---
                if processed_data:
                    st.subheader("抽出データのプレビュー（先頭1件）")
                    st.json(processed_data[0])

                    json_string = json.dumps(processed_data, ensure_ascii=False, indent=2)
                    st.download_button(
                        label="JSONデータをダウンロード",
                        data=json_string,
                        file_name="reiki_extracted_data.json",
                        mime="application/json"
                    )
                    
                except Exception as e:
                    st.error(f"エラーが発生しました: {str(e)}")
else:
    st.warning("正しいパスワードを入力すると操作パネルが表示されます。")
