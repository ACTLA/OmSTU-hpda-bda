import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import streamlit as st

st.set_page_config(page_title="Music Tracks", page_icon="🎵", layout="wide")

from app.common import inject_css

inject_css()
pg = st.navigation({
    "ВОиАД": [st.Page("views/eda.py", title="Исследовательский анализ", icon="🔍", default=True),
              st.Page("views/compare.py", title="Анализ и сопоставление данных", icon="🔬")],
    "АБД": [st.Page("views/pipeline.py", title="Загрузка данных", icon="⚙️"),
            st.Page("views/dwh.py", title="Хранилище (DWH)", icon="🏛️")],
})
pg.run()
