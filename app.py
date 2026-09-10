import streamlit as st
import pandas as pd
import sqlite3
from datetime import datetime, timedelta
import io
import re
import xml.etree.ElementTree as ET
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas

# --- CONFIGURAÇÃO DA PÁGINA ---
st.set_page_config(
    page_title="Grupo Dolcissimo - Estoque ERP", 
    page_icon="📦", 
    layout="wide",
    initial_sidebar_state="expanded"
)

# --- TEMA E CSS TECNOLÓGICO CUSTOMIZADO ---
st.markdown("""
    <style>
        /* Fundo principal limpo e moderno */
        .stApp {
            background-color: #f4f7f9;
        }
        
        /* Barra Lateral Tecnológica */
        [data-testid="stSidebar"] {
            background-color: #0d1b2a;
            color: #e0e1dd;
            border-right: 1px solid #1b263b;
        }
        [data-testid="stSidebar"] * {
            color: #e0e1dd;
        }
        
        /* Estilização dos Menus Laterais (Radio Buttons) */
        div.row-widget.stRadio > div {
            background-color: #1b263b;
            border-radius: 12px;
            padding: 15px 10px;
            border: 1px solid #415a77;
        }
        div.row-widget.stRadio > div label {
            padding: 10px;
            border-radius: 8px;
            transition: all 0.3s ease;
            cursor: pointer;
            margin-bottom: 5px;
        }
        div.row-widget.stRadio > div label:hover {
            background-color: #415a77;
            color: #00E5FF; /* Destaque Ciano */
            transform: translateX(5px);
        }
        
        /* Estilização de Botões - Efeito Gradiente e Sombra */
        .stButton>button {
            background: linear-gradient(135deg, #1b263b 0%, #415a77 100%);
            color: #ffffff;
            border: none;
            border-radius: 8px;
            font-weight: 600;
            padding: 0.5rem 1rem;
            transition: all 0.3s ease;
            box-shadow: 0 4px 6px rgba(0,0,0,0.1);
        }
        .stButton>button:hover {
            transform: translateY(-2px);
            box-shadow: 0 6px 12px rgba(27, 38, 59, 0.4);
            color: #00E5FF;
            border-color: #00E5FF;
        }

        /* Forms / Cards com bordas arredondadas e sombras suaves */
        [data-testid="stForm"] {
            background-color: #ffffff;
            border-radius: 16px;
            box-shadow: 0 8px 24px rgba(0,0,0,0.05);
            border: 1px solid #e2e8f0;
            padding: 24px;
        }

        /* Métricas (Números grandes) */
        [data-testid="stMetricValue"] {
            color: #0d1b2a;
            font-weight: 900;
            font-size: 28px;
        }
        
        /* Ajuste nas abas internas para combinar */
        .stTabs [data-baseweb="tab-list"] {
            gap: 20px;
        }
        .stTabs [data-baseweb="tab"] {
            border-radius: 8px 8px 0 0;
            padding: 10px 20px;
        }
    </style>
""", unsafe_allow_html=True)

# --- FUNÇÃO DE CONEXÃO BLINDADA ---
def get_db_connection():
    return sqlite3.connect('sistema_estoque.db', timeout=20)

# --- FUNÇÕES DE FORMATAÇÃO DE DATA ---
def formatar_data_br(val):
    if not val or pd.isna(val):
        return ""
    str_val = str(val).strip()
    try:
        if len(str_val) >= 19:
            dt = datetime.strptime(str_val[:19], "%Y-%m-%d %H:%M:%S")
            return dt.strftime("%d/%m/%Y %H:%M:%S")
        elif len(str_val) == 10:
            dt = datetime.strptime(str_val, "%Y-%m-%d")
            return dt.strftime("%d/%m/%Y")
    except:
        pass
    return str_val

def formatar_dataframe_datas(df):
    if df is None or df.empty:
        return df
    df_copia = df.copy()
    for col in df_copia.columns:
        if any(term in col.lower() for term in ['data', 'validade']):
            df_copia[col] = df_copia[col].apply(formatar_data_br)
    return df_copia

# --- FUNÇÃO FIFO COM EXTRAÇÃO INTELIGENTE DE DATAS (REGEX) ---
def descontar_estoque_fifo_com_negativo(cursor, codigo, loja, qtd_a_descontar, validade_informada):
    val_limpa = datetime.now().strftime("%Y-%m-%d")
    
    # INTELIGÊNCIA DE EXTRAÇÃO: Procura a última data no formato DD/MM/YYYY dentro de qualquer texto misturado
    if validade_informada:
        texto_validade = str(validade_informada).strip()
        datas_encontradas = re.findall(r'\d{2}/\d{2}/\d{4}', texto_validade)
        
        if datas_encontradas:
            # Pega a ÚLTIMA data encontrada na frase (que é a do excedente recém digitado)
            ultima_data_str = datas_encontradas[-1]
            try:
                val_limpa = datetime.strptime(ultima_data_str, "%d/%m/%Y").strftime("%Y-%m-%d")
            except:
                pass
        else:
            # Caso esteja no padrão americano YYYY-MM-DD
            datas_iso = re.findall(r'\d{4}-\d{2}-\d{2}', texto_validade)
            if datas_iso:
                val_limpa = datas_iso[-1]

    # Busca lotes com saldo positivo para abater primeiro (FIFO)
    cursor.execute("""
        SELECT validade, SUM(quantidade) as qtd 
        FROM estoque_lotes 
        WHERE codigo = ? AND loja = ? 
        GROUP BY validade 
        HAVING qtd > 0 
        ORDER BY validade ASC
    """, (codigo, loja))
    lotes = cursor.fetchall()
    
    restante = qtd_a_descontar
    
    # Consome os lotes disponíveis
    for val, qtd in lotes:
        if restante <= 0:
            break
        desconto = min(restante, qtd)
        cursor.execute("INSERT INTO estoque_lotes (codigo, loja, quantidade, validade) VALUES (?, ?, ?, ?)",
                       (codigo, loja, -desconto, val))
        restante -= desconto
    
    # Lança saldo negativo exatamente na validade extraída
    if restante > 0:
        cursor.execute("INSERT INTO estoque_lotes (codigo, loja, quantidade, validade) VALUES (?, ?, ?, ?)",
                       (codigo, loja, -restante, val_limpa))
    
    # Limpeza de zeros
    cursor.execute("""
        DELETE FROM estoque_lotes 
        WHERE codigo = ? AND loja = ? AND validade IN (
            SELECT validade FROM estoque_lotes 
            WHERE codigo = ? AND loja = ? 
            GROUP BY validade 
            HAVING SUM(quantidade) = 0
        )
    """, (codigo, loja, codigo, loja))

# --- INICIALIZAÇÃO DO BANCO ---
def init_db():
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("""CREATE TABLE IF NOT EXISTS lojas (nome_loja TEXT PRIMARY KEY)""")
        cursor.execute("""CREATE TABLE IF NOT EXISTS usuarios (username TEXT PRIMARY KEY, senha TEXT, perfil TEXT, loja TEXT)""")
        
        lojas_iniciais = [("Loja Centro",), ("Loja Shopping",), ("Curitiba",)]
        cursor.executemany("INSERT OR IGNORE INTO lojas VALUES (?)", lojas_iniciais)
        
        usuarios_iniciais = [
            ("admin", "admin123", "Administrador", "Geral"),
            ("gerente_centro", "123", "Gerente", "Loja Centro"),
            ("estoque_centro", "123", "Estoquista", "Loja Centro"),
            ("chefe_centro", "123", "Estoquista Chefe", "Loja Centro"),
            ("gerente_shopping", "123", "Gerente", "Loja Shopping"),
            ("estoque_shopping", "123", "Estoquista", "Loja Shopping"),
            ("chefe_shopping", "123", "Estoquista Chefe", "Loja Shopping")
        ]
        cursor.executemany("INSERT OR IGNORE INTO usuarios VALUES (?, ?, ?, ?)", usuarios_iniciais)

        cursor.execute("""CREATE TABLE IF NOT EXISTS produtos (codigo TEXT PRIMARY KEY, codigo_barras TEXT, codigo_fornecedor TEXT, descricao TEXT, categoria TEXT, unidade TEXT, custo REAL, estoque_minimo REAL DEFAULT 5.0)""")
        for col, tipo in [("codigo_barras", "TEXT"), ("codigo_fornecedor", "TEXT")]:
            try: cursor.execute(f"ALTER TABLE produtos ADD COLUMN {col} {tipo}")
            except: pass

        cursor.execute("""CREATE TABLE IF NOT EXISTS conversoes_unidades (id_conversao INTEGER PRIMARY KEY AUTOINCREMENT, codigo_produto TEXT, unidade_origem TEXT, unidade_destino TEXT, fator_conversao REAL, FOREIGN KEY(codigo_produto) REFERENCES produtos(codigo))""")
        cursor.execute("""CREATE TABLE IF NOT EXISTS estoque_lotes (id_lote INTEGER PRIMARY KEY AUTOINCREMENT, codigo TEXT, loja TEXT, quantidade REAL, validade TEXT)""")
        cursor.execute("""CREATE TABLE IF NOT EXISTS nfs_importadas (chave_nfe TEXT PRIMARY KEY, data_importacao TEXT, loja TEXT)""")
        cursor.execute("""CREATE TABLE IF NOT EXISTS requisicoes_loja (id_pedido INTEGER PRIMARY KEY AUTOINCREMENT, lote_id TEXT, data TEXT, loja TEXT, solicitante TEXT, codigo_produto TEXT, qtd_pedida REAL, qtd_enviada_estoque REAL DEFAULT 0, motivo_divergencia TEXT DEFAULT '', validade_sugerida TEXT DEFAULT '', validade_alterada_gerente TEXT DEFAULT '', qtd_entregue REAL DEFAULT 0, estoque_responsavel TEXT, status TEXT, observacao TEXT)""")
        
        colunas_req = [("qtd_enviada_estoque", "REAL DEFAULT 0"), ("motivo_divergencia", "TEXT DEFAULT ''"), ("validade_sugerida", "TEXT DEFAULT ''"), ("validade_alterada_gerente", "TEXT DEFAULT ''")]
        for col, tipo in colunas_req:
            try: cursor.execute(f"ALTER TABLE requisicoes_loja ADD COLUMN {col} {tipo}")
            except: pass

        cursor.execute("""CREATE TABLE IF NOT EXISTS inventarios_salvos (id_inventario INTEGER PRIMARY KEY AUTOINCREMENT, data TEXT, loja TEXT, responsavel TEXT, dados_csv TEXT)""")
        cursor.execute("""CREATE TABLE IF NOT EXISTS logs_sistema (id_log INTEGER PRIMARY KEY AUTOINCREMENT, data TEXT, usuario TEXT, loja TEXT, tipo_acao TEXT, detalhes TEXT)""")
        conn.commit()
    except Exception as e:
        print(f"Erro ao inicializar DB: {e}")
    finally:
        conn.close()

init_db()

# --- FUNÇÃO GERADORA DE PRÓXIMO CÓDIGO E PDF ---
def gerar_proximo_codigo_produto(cursor_existente=None):
    fecha_conn = False
    if cursor_existente: cursor = cursor_existente
    else:
        conn = get_db_connection()
        cursor = conn.cursor()
        fecha_conn = True
        
    cursor.execute("SELECT codigo FROM produtos WHERE codigo LIKE 'PROD-%'")
    res = cursor.fetchall()
    
    if not res: proximo_num = 1
    else:
        numeros = []
        for r in res:
            try: numeros.append(int(r[0].replace("PROD-", "")))
            except: pass
        proximo_num = max(numeros) + 1 if numeros else 1
        
    if fecha_conn: conn.close()
    return f"PROD-{proximo_num:03d}"

def gerar_pdf_lote_conferencia(lote_id, loja):
    conn = get_db_connection()
    df_lote_itens = pd.read_sql(f"""SELECT r.lote_id, r.data, r.solicitante, p.descricao, r.qtd_pedida, r.qtd_enviada_estoque, r.motivo_divergencia, r.validade_sugerida, r.validade_alterada_gerente FROM requisicoes_loja r JOIN produtos p ON r.codigo_produto = p.codigo WHERE r.lote_id = '{lote_id}' AND r.loja = '{loja}'""", conn)
    conn.close()

    buffer = io.BytesIO()
    c = canvas.Canvas(buffer, pagesize=letter)
    width, height = letter
    c.setFont("Helvetica-Bold", 16)
    c.drawString(50, height - 50, "GRUPO DOLCISSIMO - RELATÓRIO DE LOTE DE SEPARAÇÃO")
    
    if not df_lote_itens.empty:
        solicitante = df_lote_itens.iloc[0]['solicitante']
        data_lote = formatar_data_br(df_lote_itens.iloc[0]['data'])
        c.setFont("Helvetica", 11)
        c.drawString(50, height - 80, f"Lote ID: {lote_id} | Solicitante: {solicitante}")
        c.drawString(50, height - 100, f"Data da Solicitação: {data_lote} | Unidade: {loja}")
        c.drawString(50, height - 120, f"Data de Emissão do Relatório: {datetime.now().strftime('%d/%m/%Y %H:%M:%S')}")
        c.line(50, height - 135, width - 50, height - 135)
        
        y = height - 165
        c.setFont("Helvetica-Bold", 12)
        c.drawString(50, y, "Itens do Lote:")
        y -= 25
        
        for idx, row in df_lote_itens.iterrows():
            if y < 100:
                c.showPage()
                y = height - 50
            c.setFont("Helvetica-Bold", 10)
            c.drawString(60, y, f"• Produto: {row['descricao']}")
            y -= 15
            c.setFont("Helvetica", 9)
            c.drawString(80, y, f"Qtd Solicitada: {row['qtd_pedida']} | Qtd Enviada: {row['qtd_enviada_estoque']}")
            y -= 15
            val_usada = row['validade_alterada_gerente'] if row['validade_alterada_gerente'] else row['validade_sugerida']
            c.drawString(80, y, f"Validade: {val_usada} | Divergência: {row['motivo_divergencia'] if row['motivo_divergencia'] else 'Nenhuma'}")
            y -= 25
    c.line(50, 60, width - 50, 60)
    c.setFont("Helvetica-Oblique", 9)
    c.drawString(50, 45, "Documento gerado automaticamente pelo Sistema Corporativo de Estoque - Grupo Dolcissimo.")
    c.save()
    buffer.seek(0)
    return buffer

# --- GERENCIAMENTO DE SESSÃO PERSISTENTE ---
if 'autenticado' not in st.session_state:
    st.session_state.autenticado = False
    st.session_state.usuario = ""
    st.session_state.perfil = ""
    st.session_state.loja = ""

if not st.session_state.autenticado:
    # TELA DE LOGIN ESTILIZADA
    st.markdown("<br><br><br>", unsafe_allow_html=True)
    col1, col2, col3 = st.columns([1, 1.2, 1])
    with col2:
        st.markdown("<h1 style='text-align: center; color: #0d1b2a; font-weight: 900;'>🌐 Dolcissimo ERP</h1>", unsafe_allow_html=True)
        st.markdown("<p style='text-align: center; color: #415a77; font-size: 18px;'>Plataforma Corporativa de Gestão Logística</p>", unsafe_allow_html=True)
        
        with st.form("form_login"):
            user_input = st.text_input("Usuário (Login)")
            senha_input = st.text_input("Senha de Acesso", type="password")
            st.markdown("<br>", unsafe_allow_html=True)
            btn_login = st.form_submit_button("AUTENTICAR SISTEMA", use_container_width=True)
            
            if btn_login:
                conn = get_db_connection()
                cursor = conn.cursor()
                cursor.execute("SELECT perfil, loja FROM usuarios WHERE username = ? AND senha = ?", (user_input, senha_input))
                res = cursor.fetchone()
                conn.close()
                
                if res:
                    st.session_state.autenticado = True
                    st.session_state.usuario = user_input
                    st.session_state.perfil = res[0]
                    st.session_state.loja = res[1]
                    st.rerun()
                else:
                    st.error("❌ Credenciais inválidas. Tente novamente.")
else:
    # CARD DE USUÁRIO NA SIDEBAR
    st.sidebar.markdown(f"""
        <div style="background-color: #1b263b; padding: 20px; border-radius: 12px; text-align: center; border-left: 5px solid #00E5FF; margin-bottom: 25px; box-shadow: 0 4px 10px rgba(0,0,0,0.3);">
            <h3 style="color: #ffffff; margin: 0; padding-bottom: 5px; font-weight: bold;">👤 {st.session_state.usuario}</h3>
            <p style="color: #a3b1c6; margin: 0; font-size: 15px;">🏢 {st.session_state.loja}</p>
            <p style="color: #00E5FF; margin: 5px 0 0 0; font-size: 13px; font-weight: bold; text-transform: uppercase;">{st.session_state.perfil}</p>
        </div>
    """, unsafe_allow_html=True)

    perfil_atual = st.session_state.perfil
    loja_atual = st.session_state.loja

    # --- MENU LATERAL DINÂMICO (NOVO LAYOUT) ---
    st.sidebar.markdown("<h4 style='color: #00E5FF;'>📍 MENU DE NAVEGAÇÃO</h4>", unsafe_allow_html=True)
    
    menu_selecionado = ""
    
    if perfil_atual == "Gerente":
        menu_selecionado = st.sidebar.radio("Selecione o Módulo:", [
            "🛒 Requisição de Materiais", 
            "✔️ Check-list & Consumo", 
            "📊 Inventário Físico",
            "🚨 Alertas da Loja"
        ])
    elif perfil_atual in ["Estoquista", "Estoquista Chefe"]:
        opcoes = ["📥 Pedidos para Separação", "📦 Consulta de Estoque", "📄 Importar NF-e (XML)", "✍️ Lançamento Manual", "🚨 Alertas de Vencimento"]
        if perfil_atual == "Estoquista Chefe":
            opcoes.insert(4, "⚠️ Baixa de Avaria/Vencimento")
        menu_selecionado = st.sidebar.radio("Selecione o Módulo:", opcoes)
    elif perfil_atual == "Administrador":
        menu_selecionado = st.sidebar.radio("Selecione o Módulo (Admin):", [
            "🚨 Dashboard Global de Alertas",
            "🏢 Gestão de Unidades", 
            "👥 Controle de Acessos", 
            "✏️ Cadastro Mestre de Itens", 
            "🔄 Regras de Conversão",
            "🛠️ Ajuste de Produtos",
            "📊 Carga Lote Planilha",
            "🌐 Visão Analítica Consolidada",
            "📜 Log de Auditoria"
        ])

    st.sidebar.markdown("---")
    if st.sidebar.button("🚪 Encerrar Sessão", use_container_width=True):
        st.session_state.autenticado = False
        st.rerun()

    st.markdown(f"<h2 style='color: #0d1b2a; border-bottom: 3px solid #00E5FF; padding-bottom: 10px; margin-bottom: 30px;'>🏢 Painel Corporativo — <b>{loja_atual if perfil_atual != 'Administrador' else 'Governança Global'}</b></h2>", unsafe_allow_html=True)

    # ==========================================
    # LÓGICA DAS TELAS BASEADAS NO MENU LATERAL
    # ==========================================

    # --- GERENTE ---
    if perfil_atual == "Gerente":
        if 'carrinho_requisicao' not in st.session_state:
            st.session_state.carrinho_requisicao = []

        if menu_selecionado == "🛒 Requisição de Materiais":
            col_t1, col_t2 = st.columns([5, 1])
            col_t1.markdown("### 🛒 Nova Requisição Interna")
            if col_t2.button("🔄 Sincronizar Dados"): st.rerun()

            conn = get_db_connection()
            df_produtos = pd.read_sql(f"""SELECT p.codigo, p.codigo_barras, p.descricao, p.categoria, p.unidade, COALESCE(SUM(e.quantidade), 0) as estoque_atual, p.estoque_minimo, p.custo FROM produtos p LEFT JOIN estoque_lotes e ON p.codigo = e.codigo AND e.loja = '{loja_atual}' GROUP BY p.codigo""", conn)
            conn.close()
            
            if not df_produtos.empty:
                st.dataframe(df_produtos[['codigo', 'codigo_barras', 'descricao', 'categoria', 'estoque_atual', 'unidade']], use_container_width=True, hide_index=True)
                
                prod_sel = st.selectbox("Selecione o Produto para o Carrinho", df_produtos['descricao'].tolist())
                
                with st.form("form_add_carrinho", clear_on_submit=True):
                    qtd_pedida = st.number_input("Quantidade Desejada", min_value=0.0, value=0.0, step=1.0)
                    if prod_sel:
                        est_atual_item = df_produtos.loc[df_produtos['descricao'] == prod_sel, 'estoque_atual'].values[0]
                        unid_med = df_produtos.loc[df_produtos['descricao'] == prod_sel, 'unidade'].values[0]
                        st.info(f"💡 Em estoque: **{est_atual_item} {unid_med}**")
                        
                    if st.form_submit_button("➕ Adicionar ao Carrinho", use_container_width=True):
                        if qtd_pedida <= 0: st.warning("⚠️ Informe uma quantidade maior que zero.")
                        else:
                            cod_prod = df_produtos.loc[df_produtos['descricao'] == prod_sel, 'codigo'].values[0]
                            existente = next((item for item in st.session_state.carrinho_requisicao if item['codigo'] == cod_prod), None)
                            if existente: existente['quantidade'] += qtd_pedida
                            else: st.session_state.carrinho_requisicao.append({'codigo': cod_prod, 'descricao': prod_sel, 'quantidade': qtd_pedida})
                            st.success(f"Adicionado!")
                
                st.markdown("---")
                st.markdown("#### 📋 Itens no Carrinho Atual")
                if st.session_state.carrinho_requisicao:
                    df_carrinho = pd.DataFrame(st.session_state.carrinho_requisicao)
                    st.dataframe(df_carrinho, use_container_width=True, hide_index=True)
                    
                    col_rem1, col_rem2 = st.columns([3, 1])
                    with col_rem1: item_para_remover = st.selectbox("Selecione para remover do carrinho", df_carrinho['descricao'].tolist(), label_visibility="collapsed")
                    with col_rem2: 
                        if st.button("🗑️ Remover Item", use_container_width=True):
                            st.session_state.carrinho_requisicao = [i for i in st.session_state.carrinho_requisicao if i['descricao'] != item_para_remover]
                            st.rerun()

                    with st.form("form_finalizar_lote", clear_on_submit=True):
                        col_f1, col_f2 = st.columns(2)
                        with col_f1: nome_resp = st.text_input("Responsável pela Solicitação")
                        with col_f2: obs_lote = st.text_input("Observação Extra (Opcional)")
                        
                        if st.form_submit_button("🚀 Finalizar e Enviar Requisição ao Estoque Central", use_container_width=True):
                            if not nome_resp.strip(): st.warning("Informe o responsável.")
                            else:
                                data_hora = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                                lote_id = datetime.now().strftime("LOTE-%Y%m%d%H%M%S")
                                conn = get_db_connection()
                                try:
                                    cursor = conn.cursor()
                                    for item in st.session_state.carrinho_requisicao:
                                        cursor.execute("""INSERT INTO requisicoes_loja (lote_id, data, loja, solicitante, codigo_produto, qtd_pedida, status, observacao) VALUES (?, ?, ?, ?, ?, ?, 'Aguardando Conferência', ?)""", (lote_id, data_hora, loja_atual, nome_resp, item['codigo'], item['quantidade'], obs_lote))
                                    cursor.execute("INSERT INTO logs_sistema (data, usuario, loja, tipo_acao, detalhes) VALUES (?, ?, ?, ?, ?)", (data_hora, st.session_state.usuario, loja_atual, "REQUISICAO_LOTE", f"Requisição Lote {lote_id} enviada."))
                                    conn.commit()
                                finally: conn.close()
                                
                                carrinho_temp = st.session_state.carrinho_requisicao.copy()
                                st.session_state.carrinho_requisicao = []
                                st.success("Requisição enviada com sucesso!")
                                
                                itens_str = "%0A".join([f"- {i['quantidade']}x {i['descricao']}" for i in carrinho_temp])
                                link_whatsapp = f"https://wa.me/?text=*GRUPO DOLCISSIMO - NOVO LOTE*%0A🏢 *Unidade:* {loja_atual}%0A📋 *Lote:* {lote_id}%0A👤 *Resp:* {nome_resp}%0A📦 *Itens:*%0A{itens_str}"
                                st.markdown(f'<a href="{link_whatsapp}" target="_blank"><button style="background-color:#25D366; color:white; padding:12px 24px; border:none; border-radius:8px; font-weight:bold; width:100%;">📲 Avisar Estoque via WhatsApp</button></a>', unsafe_allow_html=True)
                else: st.info("Carrinho vazio.")
            else: st.info("Nenhum produto base cadastrado no sistema.")

        elif menu_selecionado == "✔️ Check-list & Consumo":
            st.markdown("### 🔄 Check-list de Separação & Baixa Final de Estoque")
            
            sub_chk1, sub_chk2 = st.tabs(["📌 Check-list Pendentes", "📜 Histórico de Concluídos"])
            with sub_chk1:
                conn = get_db_connection()
                df_geral_reqs = pd.read_sql(f"""SELECT r.id_pedido, r.lote_id, r.data, r.solicitante, p.descricao, r.qtd_pedida AS "Qtd Solicitada", r.qtd_enviada_estoque AS "Qtd Separada", r.validade_sugerida AS "Validade Informada", r.motivo_divergencia AS "Motivo", r.status FROM requisicoes_loja r JOIN produtos p ON r.codigo_produto = p.codigo WHERE r.loja = '{loja_atual}' ORDER BY r.id_pedido DESC""", conn)
                conn.close()
                if not df_geral_reqs.empty: st.dataframe(formatar_dataframe_datas(df_geral_reqs), use_container_width=True, hide_index=True)
                
                st.markdown("---")
                conn = get_db_connection()
                df_prontos = pd.read_sql(f"""SELECT r.id_pedido, p.descricao, r.qtd_pedida, r.qtd_enviada_estoque, r.validade_sugerida FROM requisicoes_loja r JOIN produtos p ON r.codigo_produto = p.codigo WHERE r.loja = '{loja_atual}' AND r.status = 'Pronto para Check-list'""", conn)
                conn.close()
                
                if not df_prontos.empty:
                    with st.form("form_checklist_gerente", clear_on_submit=True):
                        st.markdown("#### ✅ Validar Entrega e Deduzir Estoque")
                        id_ped_sel = st.selectbox("Selecione a ID da Separação para Finalizar", df_prontos['id_pedido'].tolist())
                        item_info = df_prontos[df_prontos['id_pedido'] == id_ped_sel].iloc[0]
                        st.info(f"**Item:** {item_info['descricao']} | **Qtd Separada Estoque:** {item_info['qtd_enviada_estoque']}")
                        
                        validade_gerente_edit = st.text_input("Data de Validade Final Conferida (Altere se necessário)", value=str(item_info['validade_sugerida']))
                        col_c1, col_c2 = st.columns(2)
                        with col_c1: qtd_entregue = st.number_input("Quantidade Real Consumida/Entregue", min_value=0.0, value=float(item_info['qtd_enviada_estoque']), step=1.0)
                        with col_c2: responsavel_baixa = st.text_input("Assinatura (Responsável)")
                        
                        if st.form_submit_button("✔️ Confirmar Baixa Definitiva do Saldo", use_container_width=True):
                            if not responsavel_baixa.strip(): st.warning("Assinatura obrigatória.")
                            else:
                                data_hora = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                                conn = get_db_connection()
                                try:
                                    cursor = conn.cursor()
                                    cursor.execute("SELECT codigo_produto FROM requisicoes_loja WHERE id_pedido = ?", (id_ped_sel,))
                                    cod_p = cursor.fetchone()[0]

                                    cursor.execute("UPDATE requisicoes_loja SET qtd_entregue = ?, validade_alterada_gerente = ?, estoque_responsavel = ?, status = 'Concluído' WHERE id_pedido = ?", (qtd_entregue, validade_gerente_edit.strip(), responsavel_baixa.strip(), id_ped_sel))
                                    
                                    # Chamada da função FIFO inteligente que puxa a data informada para o negativo
                                    descontar_estoque_fifo_com_negativo(cursor, cod_p, loja_atual, qtd_entregue, validade_gerente_edit.strip())
                                        
                                    cursor.execute("INSERT INTO logs_sistema (data, usuario, loja, tipo_acao, detalhes) VALUES (?, ?, ?, ?, ?)", (data_hora, st.session_state.usuario, loja_atual, "BAIXA_CONSUMO", f"Check-list item #{id_ped_sel} concluído por {responsavel_baixa}."))
                                    conn.commit()
                                finally: conn.close()
                                st.success("Baixa realizada e estoque subtraído com sucesso!")
                                st.rerun()
                else: st.info("Nenhum item aguardando check-list no momento.")

            with sub_chk2:
                conn = get_db_connection()
                df_hist_chk = pd.read_sql(f"""SELECT r.id_pedido, r.lote_id, r.data, r.solicitante, p.descricao, r.qtd_pedida, r.qtd_entregue, r.validade_alterada_gerente AS "Validade Validada", r.estoque_responsavel AS "Resp Check-list" FROM requisicoes_loja r JOIN produtos p ON r.codigo_produto = p.codigo WHERE r.loja = '{loja_atual}' AND r.status = 'Concluído' ORDER BY r.id_pedido DESC""", conn)
                conn.close()
                if not df_hist_chk.empty: st.dataframe(formatar_dataframe_datas(df_hist_chk), use_container_width=True, hide_index=True)
                else: st.info("Histórico vazio.")

        elif menu_selecionado == "📊 Inventário Físico":
            st.markdown("### 📊 Inventário & Correção de Saldos")
            sub_inv1, sub_inv2 = st.tabs(["Realizar Contagem", "Histórico de Planilhas"])
            
            with sub_inv1:
                conn = get_db_connection()
                df_produtos_inv = pd.read_sql("SELECT codigo, descricao FROM produtos", conn)
                conn.close()
                
                termo = st.text_input("🔍 Filtrar itens para contagem:")
                if termo: df_produtos_inv = df_produtos_inv[df_produtos_inv['descricao'].str.contains(termo, case=False, na=False)]

                if not df_produtos_inv.empty:
                    st.info("⚠️ Digite as contagens corretas. Tudo que estiver preenchido irá sobrescrever o saldo atual. Salve usando EXCLUSIVAMENTE o botão no fim da lista.")
                    contagens_usuario = {}
                    for idx, row in df_produtos_inv.iterrows():
                        col_i1, col_i2, col_i3 = st.columns([1, 3, 2])
                        col_i1.text(row['codigo'])
                        col_i2.text(row['descricao'])
                        contagens_usuario[row['codigo']] = col_i3.number_input(f"Contagem", min_value=0.0, value=0.0, step=1.0, key=f"inv_{row['codigo']}", label_visibility="collapsed")
                        st.markdown("---")

                    if st.button("💾 Sobrescrever Saldos da Unidade com Nova Contagem", use_container_width=True):
                        data_hora = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                        dados_csv = []
                        conn = get_db_connection()
                        try:
                            cursor = conn.cursor()
                            for idx, row in df_produtos_inv.iterrows():
                                c_real = contagens_usuario[row['codigo']]
                                dados_csv.append({"cód": row['codigo'], "nome": row['descricao'], "contagem": c_real})
                                
                                cursor.execute("DELETE FROM estoque_lotes WHERE loja = ? AND codigo = ?", (loja_atual, row['codigo']))
                                if c_real > 0:
                                    cursor.execute("INSERT INTO estoque_lotes (codigo, loja, quantidade, validade) VALUES (?, ?, ?, ?)", (row['codigo'], loja_atual, c_real, (datetime.now() + timedelta(days=180)).strftime("%Y-%m-%d")))
                            
                            csv_string = pd.DataFrame(dados_csv).to_csv(index=False, sep=';', encoding='utf-8-sig')
                            cursor.execute("INSERT INTO inventarios_salvos (data, loja, responsavel, dados_csv) VALUES (?, ?, ?, ?)", (data_hora, loja_atual, st.session_state.usuario, csv_string))
                            cursor.execute("INSERT INTO logs_sistema (data, usuario, loja, tipo_acao, detalhes) VALUES (?, ?, ?, ?, ?)", (data_hora, st.session_state.usuario, loja_atual, "INVENTARIO", f"Ajuste em lote."))
                            conn.commit()
                        finally: conn.close()
                        st.success("Inventário aplicado com sucesso!")
                else: st.info("Não há itens para exibir.")

            with sub_inv2:
                conn = get_db_connection()
                df_hist_inv = pd.read_sql(f"SELECT id_inventario, data, responsavel FROM inventarios_salvos WHERE loja = '{loja_atual}' ORDER BY id_inventario DESC", conn)
                conn.close()
                if not df_hist_inv.empty:
                    st.dataframe(formatar_dataframe_datas(df_hist_inv), use_container_width=True, hide_index=True)
                    inv_id_sel = st.selectbox("Selecione ID para baixar CSV:", df_hist_inv['id_inventario'].tolist())
                    if inv_id_sel:
                        conn = get_db_connection()
                        res_inv = conn.execute("SELECT dados_csv, data FROM inventarios_salvos WHERE id_inventario = ?", (inv_id_sel,)).fetchone()
                        conn.close()
                        st.download_button("📥 Baixar Planilha CSV do Inventário", data=res_inv[0], file_name=f"inv_{loja_atual}_{res_inv[1][:10]}.csv", mime="text/csv", use_container_width=True)

        elif menu_selecionado == "🚨 Alertas da Loja":
            st.markdown("### 🚨 Painel de Segurança e Vencimentos (7 dias)")
            conn = get_db_connection()
            lim = (datetime.now() + timedelta(days=7)).strftime("%Y-%m-%d")
            df_val = pd.read_sql(f"""SELECT l.codigo, p.descricao, SUM(l.quantidade) as total_qtd, l.validade FROM estoque_lotes l JOIN produtos p ON l.codigo = p.codigo WHERE l.loja = '{loja_atual}' GROUP BY l.codigo, l.validade HAVING total_qtd > 0 AND l.validade <= '{lim}'""", conn)
            df_abaixo = pd.read_sql(f"""SELECT p.codigo, p.descricao, COALESCE(SUM(e.quantidade), 0) as qtd_atual, p.estoque_minimo FROM produtos p LEFT JOIN estoque_lotes e ON p.codigo = e.codigo AND e.loja = '{loja_atual}' GROUP BY p.codigo HAVING qtd_atual < p.estoque_minimo""", conn)
            conn.close()

            c1, c2 = st.columns(2)
            with c1:
                st.markdown("#### ⏳ Vencimento Crítico")
                if not df_val.empty: st.dataframe(formatar_dataframe_datas(df_val), use_container_width=True, hide_index=True)
                else: st.success("Nenhum item próximo ao vencimento.")
            with c2:
                st.markdown("#### ⚠️ Quebra de Estoque Mínimo")
                if not df_abaixo.empty: st.dataframe(df_abaixo, use_container_width=True, hide_index=True)
                else: st.success("Abastecimento regular.")

    # --- ESTOQUISTAS ---
    elif perfil_atual in ["Estoquista", "Estoquista Chefe"]:
        
        if menu_selecionado == "📥 Pedidos para Separação":
            sub_c1, sub_c2 = st.tabs(["📦 Fila de Pedidos", "🖨️ Relatórios (Lotes)"])
            with sub_c1:
                st.markdown("### 📋 Painel de Separação de Requisições")
                conn = get_db_connection()
                df_reqs_estoque = pd.read_sql(f"""SELECT r.id_pedido, r.lote_id, r.data, r.solicitante, p.codigo, p.descricao, r.qtd_pedida, r.status FROM requisicoes_loja r JOIN produtos p ON r.codigo_produto = p.codigo WHERE r.loja = '{loja_atual}' AND r.status = 'Aguardando Conferência'""", conn)
                conn.close()

                if not df_reqs_estoque.empty:
                    st.dataframe(formatar_dataframe_datas(df_reqs_estoque), use_container_width=True, hide_index=True)
                    
                    with st.form("form_conferencia_estoquista", clear_on_submit=True):
                        id_conf = st.selectbox("Apontar ID do Pedido para Executar Separação", df_reqs_estoque['id_pedido'].tolist())
                        item_req = df_reqs_estoque[df_reqs_estoque['id_pedido'] == id_conf].iloc[0]
                        st.info(f"**Requisitado:** {item_req['qtd_pedida']}x {item_req['descricao']}")
                        
                        conn_lotes = get_db_connection()
                        df_lotes_disp = pd.read_sql(f"""SELECT validade, SUM(quantidade) as qtd FROM estoque_lotes WHERE codigo = '{item_req['codigo']}' AND loja = '{loja_atual}' GROUP BY validade HAVING qtd > 0 ORDER BY validade ASC""", conn_lotes)
                        conn_lotes.close()

                        st.markdown("#### Lotes Disponíveis:")
                        entradas_lotes = {}
                        if not df_lotes_disp.empty:
                            for i, row_lote in df_lotes_disp.iterrows():
                                val_str = row_lote['validade']
                                val_br = formatar_data_br(val_str)
                                entradas_lotes[val_str] = st.number_input(f"Separar do Lote: {val_br} (Saldo Físico: {row_lote['qtd']})", min_value=0.0, max_value=float(row_lote['qtd']), value=0.0, step=1.0, key=f"conf_lote_{i}")
                        else: st.warning("Estoque físico de lotes zerado. Use o apontamento manual abaixo para liberar excedentes.")

                        st.markdown("---")
                        col_e1, col_e2 = st.columns(2)
                        with col_e1: qtd_outra = st.number_input("Apontar Qtd Excedente (Ficará Negativo)", min_value=0.0, value=0.0, step=1.0)
                        with col_e2: val_outra = st.date_input("Validade Desta Qtd", value=None, format="DD/MM/YYYY")

                        motivo_div = st.text_input("Gatilho de Divergência (Justifique caso a qtd separada não bata com a pedida)")
                        
                        if st.form_submit_button("📤 Registrar Envio & Imprimir para Check-list", use_container_width=True):
                            qtd_total_enviada = sum(entradas_lotes.values()) + qtd_outra
                            
                            partes_val = []
                            for v_str, q in entradas_lotes.items():
                                if q > 0: partes_val.append(f"{q}x ({formatar_data_br(v_str)})")
                            if qtd_outra > 0 and val_outra: partes_val.append(f"{qtd_outra}x ({val_outra.strftime('%d/%m/%Y')})")
                            elif qtd_outra > 0 and not val_outra: partes_val.append(f"{qtd_outra}x (Data Atual)")
                            val_sug_final = " | ".join(partes_val)

                            if qtd_total_enviada == 0: st.error("Separe ao menos 1 item.")
                            elif qtd_total_enviada != float(item_req['qtd_pedida']) and not motivo_div.strip(): st.error("Divergência de quantidades exige preenchimento do motivo!")
                            else:
                                data_hora = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                                conn = get_db_connection()
                                try:
                                    cursor = conn.cursor()
                                    cursor.execute("""UPDATE requisicoes_loja SET qtd_enviada_estoque = ?, motivo_divergencia = ?, validade_sugerida = ?, status = 'Pronto para Check-list' WHERE id_pedido = ?""", (qtd_total_enviada, motivo_div.strip(), val_sug_final, id_conf))
                                    cursor.execute("INSERT INTO logs_sistema (data, usuario, loja, tipo_acao, detalhes) VALUES (?, ?, ?, ?, ?)", (data_hora, st.session_state.usuario, loja_atual, "SEPARACAO_PEDIDO", f"Separação liberada."))
                                    conn.commit()
                                finally: conn.close()
                                st.success("Encaminhado para a aba de Checklist (Painel Gerente)!")
                                st.rerun()
                else: st.info("Fila livre.")

            with sub_c2:
                conn = get_db_connection()
                df_hist_lotes = pd.read_sql(f"""SELECT DISTINCT lote_id, data, solicitante, status FROM requisicoes_loja WHERE loja = '{loja_atual}' AND status != 'Aguardando Conferência' ORDER BY id_pedido DESC""", conn)
                conn.close()
                if not df_hist_lotes.empty:
                    st.dataframe(formatar_dataframe_datas(df_hist_lotes), use_container_width=True, hide_index=True)
                    lote_sel_pdf = st.selectbox("Selecione ID do Lote para Geração de PDF Impresso", df_hist_lotes['lote_id'].tolist())
                    if lote_sel_pdf:
                        pdf_buffer = gerar_pdf_lote_conferencia(lote_sel_pdf, loja_atual)
                        st.download_button("📥 Gerar PDF de Separação", data=pdf_buffer, file_name=f"lote_{lote_sel_pdf}.pdf", mime="application/pdf", use_container_width=True)

        elif menu_selecionado == "📦 Consulta de Estoque":
            st.markdown("### 📦 Saldos Consolidados e Detalhados")
            conn = get_db_connection()
            df_estoque = pd.read_sql(f"""SELECT p.codigo, p.codigo_barras, p.codigo_fornecedor, p.descricao, p.categoria, p.unidade, COALESCE(SUM(e.quantidade), 0) as quantidade_total, p.estoque_minimo, p.custo FROM produtos p LEFT JOIN estoque_lotes e ON p.codigo = e.codigo AND e.loja = '{loja_atual}' GROUP BY p.codigo""", conn)
            conn.close()
            
            if not df_estoque.empty:
                st.dataframe(df_estoque[['codigo', 'descricao', 'quantidade_total', 'unidade', 'estoque_minimo']], use_container_width=True, hide_index=True)
                
                st.markdown("---")
                st.markdown("#### 🔍 Detalhes por Lote/Validade (Inclui Furos/Negativos)")
                prod_detalhe = st.selectbox("Analisar Lotes de:", df_estoque['descricao'].tolist())
                cod_sel = df_estoque.loc[df_estoque['descricao'] == prod_detalhe, 'codigo'].values[0]
                
                conn = get_db_connection()
                df_lotes_prod = pd.read_sql(f"""SELECT validade as Validade, SUM(quantidade) as Quantidade FROM estoque_lotes WHERE codigo = '{cod_sel}' AND loja = '{loja_atual}' GROUP BY validade HAVING Quantidade != 0 ORDER BY validade ASC""", conn)
                conn.close()
                if not df_lotes_prod.empty: st.dataframe(formatar_dataframe_datas(df_lotes_prod), use_container_width=True, hide_index=True)
                else: st.info("Item não movimentado na base de lotes.")
            
        elif menu_selecionado == "📄 Importar NF-e (XML)":
            st.markdown("### 📥 Rotina de Importação Fiscal Automática (XML)")
            xml_file = st.file_uploader("Submeta o Arquivo XML XML/NFe", type=["xml"])
            if xml_file:
                try:
                    tree = ET.parse(xml_file)
                    ns = {'nfe': 'http://www.portalfiscal.inf.br/nfe'}
                    inf_nfe = tree.getroot().find('.//nfe:infNFe', ns)
                    chave_nfe = inf_nfe.get('Id', '').replace('NFe', '') if inf_nfe is not None else tree.getroot().find('.//nfe:ide/nfe:nNF', ns).text
                    
                    conn = get_db_connection()
                    ja_importada = conn.execute("SELECT chave_nfe FROM nfs_importadas WHERE chave_nfe = ?", (chave_nfe,)).fetchone()
                    conn.close()

                    if ja_importada: st.error(f"❌ Nota {chave_nfe} recusada (Duplicidade).")
                    else:
                        itens_nf = []
                        for det in tree.getroot().findall('.//nfe:det', ns):
                            p = det.find('nfe:prod', ns)
                            itens_nf.append({
                                "Código": p.find('nfe:cProd', ns).text if p.find('nfe:cProd', ns) is not None else "S/C", 
                                "Descrição": p.find('nfe:xProd', ns).text, 
                                "Qtd": float(p.find('nfe:qCom', ns).text), 
                                "UnidadeXML": p.find('nfe:uCom', ns).text, 
                                "Custo": float(p.find('nfe:vUnCom', ns).text)
                            })
                        
                        st.info(f"Processando Chave: `{chave_nfe}` | Itens Localizados: {len(itens_nf)}")
                        with st.form("form_confirma_xml", clear_on_submit=True):
                            validades, unidades = {}, {}
                            for i, item in enumerate(itens_nf):
                                st.markdown(f"**[{item['Código']}] {item['Descrição']}** | NF Qtd: `{item['Qtd']}` | NF V.Unit: `R${item['Custo']:.2f}`")
                                c_x1, c_x2 = st.columns(2)
                                unidades[i] = c_x1.text_input("Conversão de Unid. Física (Se necessário)", value=item['UnidadeXML'], key=f"ux_{i}")
                                validades[i] = c_x2.date_input("Atribuir Validade ao Lote", key=f"vx_{i}", value=None, format="DD/MM/YYYY")
                                st.markdown("---")
                            
                            if st.form_submit_button("Confirmar Entrada Fiscal Completa", use_container_width=True):
                                data_hora = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                                conn = get_db_connection()
                                try:
                                    cursor = conn.cursor()
                                    for i, item in enumerate(itens_nf):
                                        v_str = validades[i].strftime("%Y-%m-%d") if validades[i] else (datetime.now() + timedelta(days=180)).strftime("%Y-%m-%d")
                                        cod_forn, q_real, un_fisica = item["Código"], item['Qtd'], unidades[i].strip()
                                        prod_exist = cursor.execute("SELECT codigo FROM produtos WHERE codigo_fornecedor = ?", (cod_forn,)).fetchone()
                                        
                                        if prod_exist:
                                            cod_sys = prod_exist[0]
                                            cursor.execute("UPDATE produtos SET unidade = ?, custo = ? WHERE codigo = ?", (un_fisica, item['Custo'], cod_sys))
                                        else:
                                            cod_sys = gerar_proximo_codigo_produto(cursor)
                                            cursor.execute("INSERT INTO produtos (codigo, codigo_barras, codigo_fornecedor, descricao, categoria, unidade, custo) VALUES (?, '', ?, ?, 'Geral', ?, ?)", (cod_sys, cod_forn, item['Descrição'], un_fisica, item['Custo']))
                                        
                                        cursor.execute("INSERT INTO estoque_lotes (codigo, loja, quantidade, validade) VALUES (?, ?, ?, ?)", (cod_sys, loja_atual, q_real, v_str))
                                    
                                    cursor.execute("INSERT INTO nfs_importadas (chave_nfe, data_importacao, loja) VALUES (?, ?, ?)", (chave_nfe, data_hora, loja_atual))
                                    conn.commit()
                                    st.success("Nota incorporada no ERP com sucesso!")
                                finally: conn.close()
                except Exception as e:
                    st.error(f"❌ Erro ao processar o arquivo XML: {e}")

        elif menu_selecionado == "✍️ Lançamento Manual":
            st.markdown("### ✍️ Lançamento Unitário Direto")
            conn = get_db_connection()
            df_prods_m = pd.read_sql("SELECT codigo, descricao, unidade FROM produtos", conn)
            conn.close()
            if not df_prods_m.empty:
                with st.form("form_entrada_manual", clear_on_submit=True):
                    prod_sel_m = st.selectbox("Selecione a Referência", df_prods_m['descricao'].tolist())
                    u_ref = df_prods_m.loc[df_prods_m['descricao'] == prod_sel_m, 'unidade'].values[0]
                    c1, c2, c3 = st.columns(3)
                    q_m = c1.number_input(f"Qtd ({u_ref})", min_value=0.01, step=1.0)
                    custo_m = c2.number_input("Custo Unit. (R$)", min_value=0.0)
                    v_m = c3.date_input("Validade", value=None, format="DD/MM/YYYY")
                    
                    if st.form_submit_button("Injetar Saldo", use_container_width=True):
                        c_m = df_prods_m.loc[df_prods_m['descricao'] == prod_sel_m, 'codigo'].values[0]
                        v_str = v_m.strftime("%Y-%m-%d") if v_m else (datetime.now() + timedelta(days=180)).strftime("%Y-%m-%d")
                        conn = get_db_connection()
                        try:
                            cursor = conn.cursor()
                            cursor.execute("INSERT INTO estoque_lotes (codigo, loja, quantidade, validade) VALUES (?, ?, ?, ?)", (c_m, loja_atual, q_m, v_str))
                            cursor.execute("UPDATE produtos SET custo = ? WHERE codigo = ?", (custo_m, c_m))
                            conn.commit()
                            st.success("Lançamento concluído.")
                        finally: conn.close()

        elif menu_selecionado == "⚠️ Baixa de Avaria/Vencimento":
            st.markdown("### ⚠️ Deduzir Estoque Manualmente (Contingência)")
            conn = get_db_connection()
            df_est_b = pd.read_sql(f"SELECT p.codigo, p.descricao FROM produtos p JOIN estoque_lotes e ON p.codigo = e.codigo AND e.loja = '{loja_atual}' GROUP BY p.codigo", conn)
            conn.close()
            if not df_est_b.empty:
                with st.form("form_baixa_estoque", clear_on_submit=True):
                    prod_bx = st.selectbox("Referência para Baixa", df_est_b['descricao'].tolist())
                    c1, c2, c3 = st.columns(3)
                    q_bx = c1.number_input("Qtd", min_value=0.1, step=1.0)
                    mot_bx = c2.text_input("Gatilho/Motivo (Obrigatório)")
                    val_bx = c3.date_input("Validade Afetada", value=None, format="DD/MM/YYYY")
                    
                    if st.form_submit_button("Processar Deduzimento", use_container_width=True):
                        if not mot_bx.strip(): st.error("Falta justificativa.")
                        else:
                            cod_bx = df_est_b.loc[df_est_b['descricao'] == prod_bx, 'codigo'].values[0]
                            val_str = val_bx.strftime("%Y-%m-%d") if val_bx else None
                            conn = get_db_connection()
                            try:
                                cursor = conn.cursor()
                                descontar_estoque_fifo_com_negativo(cursor, cod_bx, loja_atual, q_bx, val_str)
                                conn.commit()
                                st.success("Ajuste consolidado.")
                            finally: conn.close()

        elif menu_selecionado == "🚨 Alertas de Vencimento":
            st.markdown("### 🚨 Quadro Global de Alertas (Operação)")
            conn = get_db_connection()
            df_val = pd.read_sql(f"""SELECT l.codigo, p.descricao, SUM(l.quantidade) as total_qtd, l.validade FROM estoque_lotes l JOIN produtos p ON l.codigo = p.codigo WHERE l.loja = '{loja_atual}' GROUP BY l.codigo, l.validade HAVING total_qtd > 0 AND l.validade <= '{(datetime.now() + timedelta(days=7)).strftime("%Y-%m-%d")}'""", conn)
            conn.close()
            if not df_val.empty: st.warning("Itens no radar de criticidade:"); st.dataframe(formatar_dataframe_datas(df_val), use_container_width=True, hide_index=True)
            else: st.success("Operação fluida. Nenhum alerta pendente.")

    # --- ADMINISTRADOR ---
    elif perfil_atual == "Administrador":
        
        if menu_selecionado == "🚨 Dashboard Global de Alertas":
            st.markdown("### 🚨 Torre de Controle Corporativa")
            conn = get_db_connection()
            df_abx = pd.read_sql("SELECT e.loja, p.codigo, p.descricao, COALESCE(SUM(e.quantidade), 0) as qtd_atual, p.estoque_minimo FROM estoque_lotes e JOIN produtos p ON e.codigo = p.codigo GROUP BY e.loja, p.codigo HAVING qtd_atual < p.estoque_minimo", conn)
            df_vnc = pd.read_sql(f"SELECT e.loja, e.codigo, p.descricao, SUM(e.quantidade) as total_qtd, e.validade FROM estoque_lotes e JOIN produtos p ON e.codigo = p.codigo GROUP BY e.loja, e.codigo, e.validade HAVING total_qtd > 0 AND e.validade <= '{(datetime.now() + timedelta(days=7)).strftime("%Y-%m-%d")}'", conn)
            conn.close()
            
            c1, c2 = st.columns(2)
            with c1:
                st.markdown("#### 📉 Rupturas Identificadas")
                if not df_abx.empty: st.dataframe(df_abx, use_container_width=True, hide_index=True)
            with c2:
                st.markdown("#### ⏳ Vencimentos Iminentes (Rede)")
                if not df_vnc.empty: st.dataframe(formatar_dataframe_datas(df_vnc), use_container_width=True, hide_index=True)

        elif menu_selecionado == "🏢 Gestão de Unidades":
            st.markdown("### 🏢 Infraestrutura Logística (Lojas)")
            with st.form("form_nova_loja", clear_on_submit=True):
                nl = st.text_input("Designação Nova Unidade")
                if st.form_submit_button("Efetivar Criação") and nl.strip():
                    conn = get_db_connection()
                    conn.execute("INSERT INTO lojas VALUES (?)", (nl.strip(),)); conn.commit(); conn.close()
                    st.success("Unidade lançada no sistema.")
                    st.rerun()

        elif menu_selecionado == "👥 Controle de Acessos":
            st.markdown("### 👥 Gestão de Identidade e Perfis")
            conn = get_db_connection()
            ll = ["Geral"] + [x[0] for x in conn.execute("SELECT nome_loja FROM lojas").fetchall()]
            with st.form("form_novo_usuario", clear_on_submit=True):
                c1, c2 = st.columns(2)
                u_id = c1.text_input("User ID (Login)")
                u_pass = c1.text_input("Password", type="password")
                u_prf = c2.selectbox("Escopo", ["Gerente", "Estoquista", "Estoquista Chefe", "Administrador"])
                u_lj = c2.selectbox("Atribuição Matriz/Filial", ll)
                
                if st.form_submit_button("Gerar Credencial", use_container_width=True) and u_id:
                    conn.execute("INSERT OR REPLACE INTO usuarios VALUES (?, ?, ?, ?)", (u_id.strip(), u_pass, u_prf, u_lj)); conn.commit()
                    st.success("Sincronizado.")
            st.dataframe(pd.read_sql("SELECT * FROM usuarios", conn), use_container_width=True, hide_index=True)
            conn.close()

        elif menu_selecionado == "✏️ Cadastro Mestre de Itens":
            st.markdown("### ✏️ Padrão Base de Produtos")
            with st.form("form_cad_produto", clear_on_submit=True):
                c1, c2, c3 = st.columns(3)
                c_cod = c1.text_input("Codig. SKU Padrão")
                c_bar = c1.text_input("EAN")
                c_frn = c2.text_input("Cód. Fornecedor")
                c_dsc = c2.text_input("Descrição Descritiva")
                c_cat = c3.text_input("Categoria")
                c_und = c3.text_input("Métrica (UN, KG)")
                c_min = st.number_input("Ruptura Mínima", value=5.0)
                if st.form_submit_button("Consolidar Regra") and c_cod:
                    conn = get_db_connection()
                    conn.execute("INSERT OR REPLACE INTO produtos (codigo, codigo_barras, codigo_fornecedor, descricao, categoria, unidade, custo, estoque_minimo) VALUES (?, ?, ?, ?, ?, ?, 0, ?)", (c_cod, c_bar, c_frn, c_dsc, c_cat, c_und, c_min)); conn.commit(); conn.close()
                    st.success("Armazenado com êxito na matriz de dados.")

        elif menu_selecionado == "🔄 Regras de Conversão":
            st.info("Módulo reservado para engenharia de frações (Ex: Caixa -> Unidade). Em construção para atualização estrutural futura.")
        
        elif menu_selecionado == "🛠️ Ajuste de Produtos":
            st.markdown("### 🛠️ Editor Mestre")
            conn = get_db_connection()
            df_m = pd.read_sql("SELECT * FROM produtos", conn)
            if not df_m.empty:
                pd_sel = st.selectbox("Consultar Master Data", df_m['descricao'].tolist())
                ia = df_m[df_m['descricao'] == pd_sel].iloc[0]
                with st.form("form_edicao_produto", clear_on_submit=True):
                    e_dsc = st.text_input("Desc.", value=str(ia['descricao']))
                    e_und = st.text_input("UM", value=str(ia['unidade']))
                    e_min = st.number_input("Mínimo", value=float(ia['estoque_minimo']))
                    if st.form_submit_button("Sobrescrever Regras (Push)"):
                        conn.execute("UPDATE produtos SET descricao=?, unidade=?, estoque_minimo=? WHERE codigo=?", (e_dsc, e_und, e_min, ia['codigo']))
                        conn.commit()
                        st.success("Regras injetadas na produção.")
            conn.close()

        elif menu_selecionado == "📊 Carga Lote Planilha":
            st.markdown("### 📊 Engine de Dados Excel/CSV")
            up = st.file_uploader("Upload Massivo (.xlsx)", type=["xlsx"])
            if up:
                if st.button("Executar Batch Processing"):
                    df = pd.read_excel(up)
                    conn = get_db_connection()
                    for _, r in df.iterrows():
                        conn.execute("INSERT OR REPLACE INTO produtos (codigo, descricao, unidade, estoque_minimo) VALUES (?, ?, ?, ?)", (str(r['codigo']), str(r['descricao']), str(r['unidade']), float(r.get('estoque_minimo', 5))))
                    conn.commit(); conn.close()
                    st.success("Batch finalizado.")

        elif menu_selecionado == "🌐 Visão Analítica Consolidada":
            st.markdown("### 🌐 Power Data - Visão de Redes")
            conn = get_db_connection()
            l_todas = ["Consolidado Global"] + [x[0] for x in conn.execute("SELECT nome_loja FROM lojas").fetchall()]
            lja = st.selectbox("Filtro de Datalake", l_todas)
            
            q = "SELECT e.loja, p.codigo, p.descricao, SUM(e.quantidade) as qtd, p.unidade, e.validade FROM estoque_lotes e JOIN produtos p ON e.codigo = p.codigo GROUP BY e.loja, p.codigo, e.validade HAVING qtd != 0"
            if lja != "Consolidado Global": q = f"SELECT e.loja, p.codigo, p.descricao, SUM(e.quantidade) as qtd, p.unidade, e.validade FROM estoque_lotes e JOIN produtos p ON e.codigo = p.codigo WHERE e.loja = '{lja}' GROUP BY p.codigo, e.validade HAVING qtd != 0"
            st.dataframe(formatar_dataframe_datas(pd.read_sql(q, conn)), use_container_width=True, hide_index=True)
            conn.close()

        elif menu_selecionado == "📜 Log de Auditoria":
            st.markdown("### 📜 Tracer e Logs do Sistema (Compliance)")
            conn = get_db_connection()
            st.dataframe(formatar_dataframe_datas(pd.read_sql("SELECT * FROM logs_sistema ORDER BY id_log DESC", conn)), use_container_width=True, hide_index=True)
            conn.close()
