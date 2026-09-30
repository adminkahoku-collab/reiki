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

if password == "reiki2026": # ← 必要に応じて変更してください
    st.success("認証されました。")
    st.markdown("---")
    
    # Zipファイルアップローダー
    uploaded_file = st.file_uploader("DVD内のHTMLデータ（Zip圧縮したもの）を選択してください", type=["zip"])
    
    if uploaded_file is not None:
        if st.button("🚀 テキスト変換を開始する", type="primary"):
            with st.spinner("HTMLファイルを解析中...（数秒〜十数秒かかります）"):
                try:
                    # メモリ上でZipを展開
                    input_zip = zipfile.ZipFile(io.BytesIO(uploaded_file.read()))
                    
                    # 編ごとの抽出結果を保持する辞書
                    hen_texts = {}
                    
                    # Zip内の各HTMLファイルを処理
                    for file_info in input_zip.infolist():
                        if file_info.filename.endswith(('.html', '.htm')):
                            filename = os.path.basename(file_info.filename)
                            
                            # ファイル名から編番号を取得（例: reiki_honbun_g00100.html 等の規則に対応）
                            # ※必要に応じて分類ルールを調整可能
                            hen_name = "第01編_総務" 
                            
                            # HTML読み込みと文字コード自動判定
                            html_content = input_zip.read(file_info)
                            try:
                                html_text = html_content.decode('cp932') # Shift_JIS系
                            except:
                                html_text = html_content.decode('utf-8', errors='ignore')
                                
                            soup = BeautifulSoup(html_text, 'html.parser')
                            
                            # 不要タグの除去
                            for script in soup(["script", "style"]):
                                script.decompose()
                                
                            # プレーンテキスト（またはマークダウン）化
                            text_content = soup.get_text(separator="\n")
                            
                            if hen_name not in hen_texts:
                                hen_texts[hen_name] = ""
                            hen_texts[hen_name] += f"\n\n【例規ファイル】: {filename}\n" + text_content

                    # 出力用Zipファイルをメモリ上で作成
                    output_buffer = io.BytesIO()
                    with zipfile.ZipFile(output_buffer, "w", zipfile.ZIP_DEFLATED) as out_zip:
                        for hen_title, content in hen_texts.items():
                            out_zip.writestr(f"{hen_title}.txt", content)
                    
                    output_buffer.seek(0)
                    
                    st.success("✅ 13編のテキストデータの変換処理が正常に完了しました！")
                    
                    # ダウンロードボタン表示
                    st.download_button(
                        label="📦 変換済みテキスト（Zip）を一括ダウンロード",
                        data=output_buffer,
                        file_name="reiki_rag_texts.zip",
                        mime="application/zip"
                    )
                    
                except Exception as e:
                    st.error(f"エラーが発生しました: {str(e)}")
else:
    st.warning("正しいパスワードを入力すると操作パネルが表示されます。")
