import plotly.express as px
import streamlit as st

from modules import analysis as an
from modules.config import DATA_PATH

PALETTE = ["#6C8EA4", "#C9A27E", "#7FA38C", "#9C86B5", "#C4837D", "#8FB3B8"]
px.defaults.color_discrete_sequence = PALETTE
px.defaults.template = "plotly_white"

CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@500;700&display=swap');
h1, h2, h3 { font-family: 'Space Grotesk', sans-serif !important; letter-spacing: -0.01em; }
h1 { background: linear-gradient(90deg,#A86F52,#C9A27E); -webkit-background-clip: text; -webkit-text-fill-color: transparent; }
.kpi { padding: 16px 18px; border-radius: 14px; border: 1px solid rgba(168,111,82,.2);
       background: linear-gradient(135deg, rgba(168,111,82,.08), rgba(168,111,82,.02)); margin-bottom: 14px; }
[data-testid="stExpander"] { margin-top: 8px; }
.kpi-l { font-size: .74rem; opacity: .7; text-transform: uppercase; letter-spacing: .08em; }
.kpi-v { font-family: 'Space Grotesk', sans-serif; font-size: 1.9rem; font-weight: 700; line-height: 1.25; }
.kpi-s { font-size: .78rem; opacity: .6; }
.step { padding: 14px 16px; border-radius: 12px; border: 1px solid rgba(255,255,255,.1); background: rgba(255,255,255,.03); }
.ok { color: #06D6A0; } .todo { color: #FFD166; }
</style>
"""


def inject_css():
    st.markdown(CSS, unsafe_allow_html=True)


def kpi_row(items):
    for col, (label, value, sub) in zip(st.columns(len(items)), items):
        col.markdown(f'<div class="kpi"><div class="kpi-l">{label}</div><div class="kpi-v">{value}</div>'
                     f'<div class="kpi-s">{sub}</div></div>', unsafe_allow_html=True)


def style_fig(fig, height=360):
    fig.update_layout(paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                      colorway=PALETTE, height=height, margin=dict(l=10, r=10, t=50, b=10))
    return fig


def plot(fig, width="stretch"):
    """Plotly без подмены цветов темой Streamlit."""
    st.plotly_chart(fig, theme=None, width=width)


def _mtime():
    return DATA_PATH.stat().st_mtime if DATA_PATH.exists() else 0


@st.cache_data(show_spinner="Читаю CSV…")
def _raw(mtime):
    return an.load_raw(DATA_PATH)


@st.cache_data(show_spinner="Считаю профиль качества…")
def _profile(mtime):
    return an.run_profile(_raw(mtime))


def get_raw():
    return _raw(_mtime())


def get_profile():
    return _profile(_mtime())
