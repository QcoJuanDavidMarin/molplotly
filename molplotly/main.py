from io import BytesIO
import os
import base64
import textwrap

import numpy as np
from rdkit import Chem
from rdkit.Chem.Draw import rdMolDraw2D

from jupyter_dash import JupyterDash

import plotly.express as px
from dash import dcc, html, Input, Output, no_update

from html.parser import HTMLParser
import copy
from plotly.graph_objects import Figure

import requests


def str2bool(v):
    return v.lower() in ("yes", "true", "t", "1")


def patch_file(file_path: str, content: bytes, extra: dict = None) -> bytes:
    if file_path == "index.html":
        index_html_content = content.decode("utf8")
        extra_jsons = f"""
        var patched_jsons_content={{
        {','.join(["'/" + k + "':" + v.decode("utf8") + "" for k, v in extra.items()])}
        }};
        """
        patched_content = (
            index_html_content.replace(
                "<footer>",
                f"""
            <footer>
            <script>
            """
                + extra_jsons
                + """
            const origFetch = window.fetch;
            window.fetch = function () {
                const e = arguments[0]
                if (patched_jsons_content.hasOwnProperty(e)) {
                    return Promise.resolve({
                        json: () => Promise.resolve(patched_jsons_content[e]),
                        headers: new Headers({'content-type': 'application/json'}),
                        status: 200,
                    });
                } else {
                    return origFetch.apply(this, arguments)
                }
            }
            </script>
            """,
            )
            .replace('href="/', 'href="')
            .replace('src="/', 'src="')
        )
        return patched_content.encode("utf8")
    else:
        return content


def write_file(
    file_path: str,
    content: bytes,
    target_dir="target",
):
    target_file_path = os.path.join(target_dir, file_path.lstrip("/").split("?")[0])
    target_leaf_dir = os.path.dirname(target_file_path)
    os.makedirs(target_leaf_dir, exist_ok=True)
    with open(target_file_path, "wb") as f:
        f.write(content)
    pass


class ExternalResourceParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.resources = []

    def handle_starttag(self, tag, attrs):
        if tag == "link":
            for k, v in attrs:
                if k == "href":
                    self.resources.append(v)
        if tag == "script":
            for k, v in attrs:
                if k == "src":
                    self.resources.append(v)


def make_static(base_url, target_dir="target"):
    index_html_bytes = requests.get(base_url).content
    json_paths = [
        "_dash-layout",
        "_dash-dependencies",
    ]
    extra_json = {}
    for json_path in json_paths:
        json_content = requests.get(base_url + json_path).content
        extra_json[json_path] = json_content

    patched_bytes = patch_file("index.html", index_html_bytes, extra=extra_json)
    write_file("index.html", patched_bytes, target_dir)
    parser = ExternalResourceParser()
    parser.feed(patched_bytes.decode("utf8"))
    extra_js = [
        "_dash-component-suites/dash/dcc/async-graph.js",
        "_dash-component-suites/dash/dcc/async-plotlyjs.js",
        "_dash-component-suites/dash/dash_table/async-table.js",
        "_dash-component-suites/dash/dash_table/async-highlight.js",
    ]
    for resource_url in parser.resources + extra_js:
        resource_url_full = base_url + resource_url
        print(f"get {resource_url_full}")
        resource_bytes = requests.get(resource_url_full).content
        patched_bytes = patch_file(resource_url, resource_bytes)
        write_file(resource_url, patched_bytes, target_dir)


class molplotly_figure(object):
    def __init__(self, fig, df):
        self.fig = fig
        self.df = df

    def __getattr__(self, name):
        try:
            return getattr(self.fig, name)
        except AttributeError:
            raise AttributeError(
                "molplotly_figure' object has no attribute '%s'" % name
            )


# class molplotly_figure():
#     def __init__(self, fig, df):
#         self.fig = fig
#         self.df = df


def add_molecules(
    fig,
    df,
    smiles_col="SMILES",
    show_img=True,
    svg_size=200,
    alpha=0.75,
    img_alpha=0.7,
    title_col=None,
    show_coords=True,
    caption_cols=None,
    caption_transform={},
    color_col=None,
    wrap=True,
    wraplen=20,
    width=150,
    fontfamily="Arial",
    fontsize=12,
    port=8081,
):
    """
    A function that takes a plotly figure and a dataframe with molecular SMILES
    and returns a dash app that dynamically generates an image of molecules in the hover box
    when hovering the mouse over datapoints.
    ...

    Attributes
    ----------
    fig : plotly.graph_objects.Figure object
        a plotly figure object containing datapoints plotted from df
    df : pandas.DataFrame object
        a pandas dataframe that contains the data plotted in fig
    smiles_col : str, optional
        name of the column in df containing the smiles plotted in fig (default 'SMILES')
    show_img : bool, optional
        whether or not to generate the molecule image in the dash app (default True)
    title_col : str, optional
        name of the column in df to be used as the title entry in the hover box (default None)
    show_coords : bool, optional
        whether or not to show the coordinates of the data point in the hover box (default True)
    caption_cols : list, optional
        list of column names in df to be included in the hover box (default None)
    caption_transform : dict, optional
        Functions applied to specific items in all cells. The dict must follow a key: function structure where the key must correspond to one of the columns in subset or tooltip. (default {})
    color_col : str, optional
        name of the column in df that is used to color the datapoints in df - necessary when there is discrete conditional coloring (default None)
    wrap : bool, optional
        whether or not to wrap the title text to multiple lines if the length of the text is too long (default True)
    wraplen : int, optional
        the threshold length of the title text before wrapping begins - adjust when changing the width of the hover box (default 20)
    width : int, optional
        the width in pixels of the hover box (default 150)
    fontfamily : str, optional
        the font family used in the hover box (default 'Arial')
    fontsize : int, optional
        the font size used in the hover box - the font of the title line is fontsize+2 (default 12)
    """
    if isinstance(smiles_col, str):
        smiles_col = [smiles_col]

    if len(smiles_col) > 1:
        menu = dcc.Dropdown(
            options=[{"label": x, "value": x} for x in smiles_col],
            value=smiles_col[0],
            multi=True,
            id="smiles-menu",
        )
        # slider = dcc.Slider(
        #     min=0,
        #     max=len(smiles_col) - 1,
        #     step=1,
        #     marks={i: smiles_col[i] for i in range(len(smiles_col))},
        #     value=0,
        #     id="smiles-slider",
        # )
    else:
        menu = dcc.Store(id="smiles-menu", data=0)
        # slider = dcc.Store(id="smiles-slider", data=0)

    df_data = df[smiles_col].copy()
    # df_data.drop_duplicates(subset=smiles_col)

    for i, row in df_data.iterrows():
        for col in smiles_col:
            # print(smiles)
            # print(row)
            # print(col)
            buffered = BytesIO()
            d2d = rdMolDraw2D.MolDraw2DSVG(svg_size, svg_size)
            opts = d2d.drawOptions()
            opts.clearBackground = False
            d2d.DrawMolecule(Chem.MolFromSmiles(row[col]))
            d2d.FinishDrawing()
            img_str = d2d.GetDrawingText()
            buffered.write(str.encode(img_str))
            img_str = base64.b64encode(buffered.getvalue())
            img_str = "data:image/svg+xml;base64,{}".format(repr(img_str)[2:-1])

            df_data.loc[i, f"{col}_img"] = img_str

    # fig.dataframe = df_data
    colors = {0: "black"}
    if len(fig.data) != 1:
        if color_col is not None:
            colors = {index: x.marker["color"] for index, x in enumerate(fig.data)}
            if df[color_col].dtype == bool:
                curve_dict = {
                    index: str2bool(x["name"]) for index, x in enumerate(fig.data)
                }
            elif df[color_col].dtype == int:
                curve_dict = {index: int(x["name"]) for index, x in enumerate(fig.data)}
            else:
                curve_dict = {index: x["name"] for index, x in enumerate(fig.data)}
        else:
            raise ValueError(
                "color_col needs to be specified if there is more than one plotly curve in the figure!"
            )

    # my_fig = molplotly_figure(fig, df_data)
    # my_fig.fig.update_traces(hoverinfo="none", hovertemplate=None)
    fig.update_traces(hoverinfo="none", hovertemplate=None)

    app = JupyterDash(__name__)
    app.layout = html.Div(
        [
            menu,
            # dcc.Graph(id="graph-basic-2", figure=my_fig.fig, clear_on_unhover=True),
            dcc.Graph(id="graph-basic-2", figure=fig, clear_on_unhover=True),
            dcc.Tooltip(
                id="graph-tooltip", background_color=f"rgba(255,255,255,{alpha})"
            ),
            # slider,
            # menu,
        ]
    )
    # [
    #     html.Button("save static", id="save", n_clicks=0),
    #     html.Span("", id="saved"),
    #     dcc.Graph(id="graph-basic-2", figure=my_fig.fig, clear_on_unhover=True),
    #     dcc.Tooltip(
    #         id="graph-tooltip", background_color=f"rgba(255,255,255,{alpha})"
    #     ),
    # ]

    @app.callback(
        output=[
            Output("graph-tooltip", "show"),
            Output("graph-tooltip", "bbox"),
            Output("graph-tooltip", "children"),
        ],
        inputs=[
            Input("graph-basic-2", "hoverData"),
            Input("smiles-menu", "value"),
        ],
    )
    def display_hover(hoverData, value):
        if hoverData is None:
            return False, no_update, no_update

        if isinstance(value, str):
            chosen_smiles = [value]
        else:
            chosen_smiles = value
        # print(chosen_smiles)
        pt = hoverData["points"][0]
        bbox = pt["bbox"]
        num = pt["pointNumber"]
        curve_num = pt["curveNumber"]

        if len(fig.data) != 1:
            df_curve = df[df[color_col] == curve_dict[curve_num]].reset_index(drop=True)
            df_row = df_curve.iloc[num]
        else:
            df_row = df.iloc[num]

        hoverbox_elements = []

        if show_img:
            # # The 2D image of the molecule is generated here
            for col in smiles_col:
                if col in chosen_smiles:
                    # print(df_row)
                    smiles = df_row[col]
                    img_str = df_data.query(f"{col} == @smiles")[f"{col}_img"].values[0]

                    hoverbox_elements.append(
                        html.Img(
                            src=img_str,
                            style={
                                "width": "100%",
                                "background-color": f"rgba(255,255,255,{img_alpha})",
                            },
                        )
                    )

        if title_col is not None:
            title = df_row[title_col]
            if len(title) > wraplen:
                if wrap:
                    title = textwrap.fill(title, width=wraplen)
                else:
                    title = title[:wraplen] + "..."
            hoverbox_elements.append(
                html.H2(
                    f"{title}",
                    style={
                        "color": colors[curve_num],
                        "font-family": fontfamily,
                        "fontSize": fontsize + 2,
                    },
                )
            )
        if show_coords:
            x_label = fig.layout.xaxis.title.text
            y_label = fig.layout.yaxis.title.text
            if x_label in caption_transform:
                style_str = caption_transform[x_label](pt["x"])
                hoverbox_elements.append(
                    html.P(
                        f"{x_label} : {style_str}",
                        style={
                            "color": "black",
                            "font-family": fontfamily,
                            "fontSize": fontsize,
                        },
                    )
                )
            else:
                hoverbox_elements.append(
                    html.P(
                        f"{x_label}: {pt['x']}",
                        style={
                            "color": "black",
                            "font-family": fontfamily,
                            "fontSize": fontsize,
                        },
                    )
                )
            if y_label in caption_transform:
                style_str = caption_transform[y_label](pt["y"])
                hoverbox_elements.append(
                    html.P(
                        f"{y_label} : {style_str}",
                        style={
                            "color": "black",
                            "font-family": fontfamily,
                            "fontSize": fontsize,
                        },
                    )
                )
            else:
                hoverbox_elements.append(
                    html.P(
                        f"{y_label} : {pt['y']}",
                        style={
                            "color": "black",
                            "font-family": fontfamily,
                            "fontSize": fontsize,
                        },
                    )
                )
        if caption_cols is not None:
            for caption in caption_cols:
                caption_val = df_row[caption]
                if caption in caption_transform:
                    style_str = caption_transform[caption](caption_val)
                    hoverbox_elements.append(
                        html.P(
                            f"{caption} : {style_str}",
                            style={
                                "color": "black",
                                "font-family": fontfamily,
                                "fontSize": fontsize,
                            },
                        )
                    )
                else:
                    hoverbox_elements.append(
                        html.P(
                            f"{caption} : {caption_val}",
                            style={
                                "color": "black",
                                "font-family": fontfamily,
                                "fontSize": fontsize,
                            },
                        )
                    )
        children = [
            html.Div(
                hoverbox_elements,
                style={
                    "width": f"{width}px",
                    "white-space": "normal",
                },
            )
        ]

        return True, bbox, children

    # @app.callback(
    #     Output("saved", "children"),
    #     Input("save", "n_clicks"),
    # )
    # def save_result(n_clicks):
    #     if n_clicks == 0:
    #         return "not saved"
    #     else:
    #         make_static(f"http://127.0.0.1:{port}/")
    #         return "saved"

    return app
