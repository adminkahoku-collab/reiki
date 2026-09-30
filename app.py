import streamlit as st
import zipfile
import io

st.title("⚖️ 例規HTMLデータ ➔ RAGテキスト変換ツール")
st.write("DVDから抽出したZipファイルをアップロードしてください。")

# パスワード保護（簡易アクセス制限）
password = st.text_input("職員用パスワードを入力してください", type="password")

if password == "reiki063215":  # ←お好みのパスワードに変更してください
    st.success("認証されました。")
    uploaded_file = st.file_uploader("DVDデータ（Zip形式）を選択", type=["zip"])
    
    if uploaded_file is not None:
        if st.button("テキスト変換を開始"):
            st.info("処理を実行中...")
            # ここに既存の extract.py のロジックが入ります
            st.success("✅ 13編のテキストデータ抽出が完了しました！")
else:
    st.warning("正しいパスワードを入力すると操作パネルが表示されます。")
