import streamlit as st
import pandas as pd
import sqlite3
from datetime import datetime, timedelta
import io
import xml.etree.ElementTree as ET
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas

# --- CONFIGURAÇÃO DA PÁGINA ---
st.set_page_config(
    page_title="Grupo Dolcissimo - Sistema de Estoque", 
    page_icon="📦", 
    layout="wide",
    initial_sidebar_state="expanded"
)

# --- CSS PERSONALIZADO ---
st.markdown("""
    <style>
        .main { background-color: #f8f9fa; }
        .stButton>button { border-radius: 8px; font-weight: bold; transition: 0.3s; }
        .stButton>button:hover { border-color: #25D366; color: #25D366; }
        div[data-testid="stMetricValue"] { font-size: 24px; color: #2c3e50; }
    </style>
""", unsafe_allow_html=True)

# --- FUNÇÃO DE CONEXÃO BLINDADA ---
def get_db_connection():
    return sqlite3.connect('sistema_estoque.db', timeout=20)

# --- FUNÇÃO DE FORMATAÇÃO DE DATA PARA BR (DD/MM/AAAA) ---
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

# --- FUNÇÃO FIFO COM PERMISSÃO DE SALDO NEGATIVO POR VALIDADE ---
def descontar_estoque_fifo_com_negativo(cursor, codigo, loja, qtd_a_descontar, validade_informada):
    # Busca lotes com saldo positivo para abater primeiro (FIFO)
    cursor.execute("""
        id_lote, validade, quantidade 
        FROM estoque_lotes 
        WHERE codigo = ? AND loja = ? AND quantidade > 0 
        ORDER BY validade ASC
    """, (codigo, loja))
    # Correção da query para compatibilidade segura
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
    
    # Abate dos lotes existentes mais antigos
    for val, qtd in lotes:
        if restante <= 0:
            break
        desconto = min(restante, qtd)
        cursor.execute("INSERT INTO estoque_lotes (codigo, loja, quantidade, validade) VALUES (?, ?, ?, ?)",
                       (codigo, loja, -desconto, val))
        restante -= desconto
    
    # Se ainda sobrar quantidade a descontar (estoque insuficiente), lança o restante negativo na validade informada/escolhida
    if restante > 0:
        val_str = validade_informada if validade_informada else datetime.now().strftime("%Y-%m-%d")
        cursor.execute("INSERT INTO estoque_lotes (codigo, loja, quantidade, validade) VALUES (?, ?, ?, ?)",
                       (codigo, loja, -restante, val_str))
    
    # Limpeza de registros zerados consolidados por validade
    cursor.execute("""
        DELETE FROM estoque_lotes 
        WHERE codigo = ? AND loja = ? AND validade IN (
            SELECT validade FROM estoque_lotes 
            WHERE codigo = ? AND loja = ? 
            GROUP BY validade 
            HAVING SUM(quantidade) <= 0
        )
    """, (codigo, loja, codigo, loja))

# --- CONFIGURAÇÃO DO BANCO DE DADOS ---
def init_db():
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        
        cursor.execute("""CREATE TABLE IF NOT EXISTS lojas (
                            nome_loja TEXT PRIMARY KEY)""")
        
        cursor.execute("""CREATE TABLE IF NOT EXISTS usuarios (
                            username TEXT PRIMARY KEY,
                            senha TEXT,
                            perfil TEXT,
                            loja TEXT)""")
        
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

        cursor.execute("""CREATE TABLE IF NOT EXISTS produtos (
                            codigo TEXT PRIMARY KEY, 
                            codigo_barras TEXT,
                            codigo_fornecedor TEXT,
                            descricao TEXT, 
                            categoria TEXT, 
                            unidade TEXT, 
                            custo REAL,
                            estoque_minimo REAL DEFAULT 5.0)""")

        for col, tipo in [("codigo_barras", "TEXT"), ("codigo_fornecedor", "TEXT")]:
            try:
                cursor.execute(f"ALTER TABLE produtos ADD COLUMN {col} {tipo}")
            except:
                pass

        cursor.execute("""CREATE TABLE IF NOT EXISTS conversoes_unidades (
                            id_conversao INTEGER PRIMARY KEY AUTOINCREMENT,
                            codigo_produto TEXT,
                            unidade_origem TEXT,
                            unidade_destino TEXT,
                            fator_conversao REAL,
                            FOREIGN KEY(codigo_produto) REFERENCES produtos(codigo))""")

        cursor.execute("""CREATE TABLE IF NOT EXISTS estoque_lotes (
                            id_lote INTEGER PRIMARY KEY AUTOINCREMENT,
                            codigo TEXT,
                            loja TEXT,
                            quantidade REAL,
                            validade TEXT)""")

        cursor.execute("""CREATE TABLE IF NOT EXISTS nfs_importadas (
                            chave_nfe TEXT PRIMARY KEY,
                            data_importacao TEXT,
                            loja TEXT)""")

        cursor.execute("""CREATE TABLE IF NOT EXISTS requisicoes_loja (
                            id_pedido INTEGER PRIMARY KEY AUTOINCREMENT,
                            lote_id TEXT,
                            data TEXT,
                            loja TEXT,
                            solicitante TEXT,
                            codigo_produto TEXT,
                            qtd_pedida REAL,
                            qtd_enviada_estoque REAL DEFAULT 0,
                            motivo_divergencia TEXT DEFAULT '',
                            validade_sugerida TEXT DEFAULT '',
                            validade_alterada_gerente TEXT DEFAULT '',
                            qtd_entregue REAL DEFAULT 0,
                            estoque_responsavel TEXT,
                            status TEXT,
                            observacao TEXT)""")
        
        colunas_req = [("qtd_enviada_estoque", "REAL DEFAULT 0"), 
                       ("motivo_divergencia", "TEXT DEFAULT ''"), 
                       ("validade_sugerida", "TEXT DEFAULT ''"), 
                       ("validade_alterada_gerente", "TEXT DEFAULT ''")]
        for col, tipo in colunas_req:
            try:
                cursor.execute(f"ALTER TABLE requisicoes_loja ADD COLUMN {col} {tipo}")
            except:
                pass

        cursor.execute("""CREATE TABLE IF NOT EXISTS inventarios_salvos (
                            id_inventario INTEGER PRIMARY KEY AUTOINCREMENT,
                            data TEXT,
                            loja TEXT,
                            responsavel TEXT,
                            dados_csv TEXT)""")

        cursor.execute("""CREATE TABLE IF NOT EXISTS logs_sistema (
                            id_log INTEGER PRIMARY KEY AUTOINCREMENT,
                            data TEXT,
                            usuario TEXT,
                            loja TEXT,
                            tipo_acao TEXT,
                            detalhes TEXT)""")
        
        conn.commit()
    except Exception as e:
        print(f"Erro ao inicializar DB: {e}")
    finally:
        conn.close()

init_db()

# --- FUNÇÃO GERADORA DE PRÓXIMO CÓDIGO AUTOMÁTICO ---
def gerar_proximo_codigo_produto(cursor_existente=None):
    fecha_conn = False
    if cursor_existente:
        cursor = cursor_existente
    else:
        conn = get_db_connection()
        cursor = conn.cursor()
        fecha_conn = True
        
    cursor.execute("SELECT codigo FROM produtos WHERE codigo LIKE 'PROD-%'")
    res = cursor.fetchall()
    
    if not res:
        proximo_num = 1
    else:
        numeros = []
        for r in res:
            try:
                num = int(r[0].replace("PROD-", ""))
                numeros.append(num)
            except:
                pass
        proximo_num = max(numeros) + 1 if numeros else 1
        
    if fecha_conn:
        conn.close()
        
    return f"PROD-{proximo_num:03d}"

# --- FUNÇÃO GERADORA DE PDF AGRUPADO POR LOTE ---
def gerar_pdf_lote_conferencia(lote_id, loja):
    conn = get_db_connection()
    df_lote_itens = pd.read_sql(f"""
        SELECT r.lote_id, r.data, r.solicitante, p.descricao, r.qtd_pedida, r.qtd_enviada_estoque, r.motivo_divergencia, r.validade_sugerida, r.validade_alterada_gerente
        FROM requisicoes_loja r
        JOIN produtos p ON r.codigo_produto = p.codigo
        WHERE r.lote_id = '{lote_id}' AND r.loja = '{loja}'
    """, conn)
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

st.sidebar.title("🔐 Acesso ao Sistema")

if not st.session_state.autenticado:
    col1, col2, col3 = st.columns([1, 2, 1])
    with col2:
        st.markdown("<h1 style='text-align: center; color: #2c3e50;'>📦 Grupo Dolcissimo</h1>", unsafe_allow_html=True)
        st.markdown("<h3 style='text-align: center; color: #7f8c8d;'>Sistema Corporativo de Gestão de Estoque</h3>", unsafe_allow_html=True)
        st.markdown("---")
        
        with st.form("form_login"):
            user_input = st.text_input("👤 Usuário")
            senha_input = st.text_input("🔑 Senha", type="password")
            btn_login = st.form_submit_button("Entrar no Sistema", use_container_width=True)
            
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
                    st.error("❌ Usuário ou senha incorretos!")
else:
    st.sidebar.markdown(f"👤 **Usuário:** {st.session_state.usuario}")
    st.sidebar.markdown(f"🏢 **Unidade:** {st.session_state.loja}")
    st.sidebar.markdown(f"🔑 **Perfil:** {st.session_state.perfil}")
    st.sidebar.markdown("---")
    
    if st.sidebar.button("🚪 Sair do Sistema", use_container_width=True):
        st.session_state.autenticado = False
        st.rerun()

    perfil_atual = st.session_state.perfil
    loja_atual = st.session_state.loja

    st.markdown(f"## 🏢 Painel Corporativo — **{loja_atual if perfil_atual != 'Administrador' else 'Global (Admin)'}**")
    st.markdown("---")

    # --- 1. MÓDULO: GERENTE ---
    if perfil_atual == "Gerente":
        tab_ped, tab_check, tab_inv, tab_alertas = st.tabs([
            "📝 Requisição Interna", 
            "✔️ Check-list & Baixa de Consumo", 
            "📋 Inventário & Histórico",
            "🚨 Alertas & Vencimentos"
        ])
        
        if 'carrinho_requisicao' not in st.session_state:
            st.session_state.carrinho_requisicao = []

        with tab_ped:
            col_t1, col_t2 = st.columns([5, 1])
            with col_t1:
                st.markdown("### 🛒 Requisição de Materiais (Consumo Próprio)")
            with col_t2:
                if st.button("🔄 Atualizar", key="ref_g_ped"):
                    st.rerun()

            conn = get_db_connection()
            df_produtos = pd.read_sql(f"""
                SELECT p.codigo, p.codigo_barras, p.descricao, p.categoria, p.unidade, COALESCE(SUM(e.quantidade), 0) as estoque_atual, p.estoque_minimo, p.custo
                FROM produtos p
                LEFT JOIN estoque_lotes e ON p.codigo = e.codigo AND e.loja = '{loja_atual}'
                GROUP BY p.codigo
            """, conn)
            conn.close()
            
            if not df_produtos.empty:
                st.dataframe(
                    df_produtos[['codigo', 'codigo_barras', 'descricao', 'categoria', 'estoque_atual', 'unidade', 'estoque_minimo', 'custo']], 
                    use_container_width=True,
                    hide_index=True
                )
                
                prod_sel = st.selectbox("Selecione o Produto", df_produtos['descricao'].tolist(), key="select_prod_req")
                
                with st.form("form_add_carrinho"):
                    qtd_pedida = st.number_input("Quantidade Desejada", min_value=0.0, value=0.0, step=1.0)
                    
                    if prod_sel:
                        est_atual_item = df_produtos.loc[df_produtos['descricao'] == prod_sel, 'estoque_atual'].values[0]
                        unid_med = df_produtos.loc[df_produtos['descricao'] == prod_sel, 'unidade'].values[0]
                        st.info(f"ℹ️ Estoque atual desta unidade para **{prod_sel}**: **{est_atual_item} {unid_med}**")
                        
                    btn_add = st.form_submit_button("➕ Adicionar ao Carrinho", use_container_width=True)
                    
                    if btn_add:
                        if qtd_pedida <= 0:
                            st.warning("⚠️ Informe uma quantidade maior que zero.")
                        else:
                            cod_prod = df_produtos.loc[df_produtos['descricao'] == prod_sel, 'codigo'].values[0]
                            existente = next((item for item in st.session_state.carrinho_requisicao if item['codigo'] == cod_prod), None)
                            if existente:
                                existente['quantidade'] += qtd_pedida
                            else:
                                st.session_state.carrinho_requisicao.append({'codigo': cod_prod, 'descricao': prod_sel, 'quantidade': qtd_pedida})
                            st.success(f"Item '{prod_sel}' adicionado com sucesso!")
                
                st.markdown("---")
                st.markdown("### 📋 Itens no Carrinho Atual")
                if st.session_state.carrinho_requisicao:
                    df_carrinho = pd.DataFrame(st.session_state.carrinho_requisicao)
                    st.dataframe(df_carrinho, use_container_width=True, hide_index=True)
                    
                    col_rem1, col_rem2 = st.columns([2, 1])
                    with col_rem1:
                        item_para_remover = st.selectbox("Selecione para remover", df_carrinho['descricao'].tolist(), key="rem_item_req", label_visibility="collapsed")
                    with col_rem2:
                        if st.button("🗑️ Remover Item", use_container_width=True):
                            st.session_state.carrinho_requisicao = [item for item in st.session_state.carrinho_requisicao if item['descricao'] != item_para_remover]
                            st.rerun()

                    with st.form("form_finalizar_lote"):
                        col_f1, col_f2 = st.columns(2)
                        with col_f1:
                            nome_resp = st.text_input("Seu Nome (Responsável)")
                        with col_f2:
                            obs_lote = st.text_input("Observação Geral (Opcional)")
                        
                        btn_enviar_lote = st.form_submit_button("🚀 Finalizar e Enviar Requisição", use_container_width=True)
                        if btn_enviar_lote:
                            if not nome_resp.strip():
                                st.warning("Por favor, informe o seu nome.")
                            else:
                                data_hora = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                                lote_id = datetime.now().strftime("LOTE-%Y%m%d%H%M%S")
                                
                                conn = get_db_connection()
                                try:
                                    cursor = conn.cursor()
                                    for item in st.session_state.carrinho_requisicao:
                                        cursor.execute("""INSERT INTO requisicoes_loja (lote_id, data, loja, solicitante, codigo_produto, qtd_pedida, status, observacao) 
                                                          VALUES (?, ?, ?, ?, ?, ?, 'Aguardando Conferência', ?)""",
                                                       (lote_id, data_hora, loja_atual, nome_resp, item['codigo'], item['quantidade'], obs_lote))
                                    
                                    desc_log = f"Requisição Lote {lote_id} ({len(st.session_state.carrinho_requisicao)} itens). Solicitante: {nome_resp}."
                                    cursor.execute("INSERT INTO logs_sistema (data, usuario, loja, tipo_acao, detalhes) VALUES (?, ?, ?, ?, ?)",
                                                   (data_hora, st.session_state.usuario, loja_atual, "REQUISICAO_LOTE", desc_log))
                                    conn.commit()
                                finally:
                                    conn.close()
                                
                                carrinho_temp = st.session_state.carrinho_requisicao.copy()
                                st.session_state.carrinho_requisicao = []
                                st.success("Requisição enviada com sucesso!")
                                
                                itens_str = "%0A".join([f"- {i['quantidade']}x {i['descricao']}" for i in carrinho_temp])
                                texto_zap = f"*GRUPO DOLCISSIMO - REQUISIÇÃO LOJA*%0A" \
                                            f"--------------------------------------------------%0A" \
                                            f"🏢 *Unidade:* {loja_atual}%0A" \
                                            f"📋 *Lote:* {lote_id}%0A" \
                                            f"📅 *Data:* {formatar_data_br(data_hora)}%0A" \
                                            f"👤 *Solicitante:* {nome_resp}%0A" \
                                            f"📦 *Itens:*%0A{itens_str}%0A" \
                                            f"💬 *Obs:* {obs_lote if obs_lote else 'Nenhuma'}"
                                
                                link_whatsapp = f"https://wa.me/?text={texto_zap}"
                                st.markdown(f'<a href="{link_whatsapp}" target="_blank"><button style="background-color:#25D366; color:white; padding:12px 24px; border:none; border-radius:8px; font-weight:bold; font-size:16px; cursor:pointer; width:100%;">📲 Enviar Pedido via WhatsApp</button></a>', unsafe_allow_html=True)
                else:
                    st.info("O carrinho de requisição está vazio.")
            else:
                st.info("Nenhum produto cadastrado.")
                
        with tab_check:
            col_t1, col_t2 = st.columns([5, 1])
            with col_t1:
                st.markdown("### 🔄 Check-list & Baixa de Consumo/Venda")
            with col_t2:
                if st.button("🔄 Atualizar", key="ref_g_chk"):
                    st.rerun()
            
            sub_chk1, sub_chk2 = st.tabs(["Realizar Confirmação (Pendente)", "📜 Histórico de Check-lists (Validação Concluída)"])

            with sub_chk1:
                conn = get_db_connection()
                df_geral_reqs = pd.read_sql(f"""
                    SELECT r.id_pedido, r.lote_id, r.data, r.solicitante, p.descricao, 
                           r.qtd_pedida AS "Qtd Solicitada", 
                           r.qtd_enviada_estoque AS "Qtd Separada", 
                           r.validade_sugerida AS "Validade Informada",
                           r.motivo_divergencia AS "Motivo Divergência", 
                           r.status
                    FROM requisicoes_loja r
                    JOIN produtos p ON r.codigo_produto = p.codigo
                    WHERE r.loja = '{loja_atual}'
                    ORDER BY r.id_pedido DESC
                """, conn)
                conn.close()
                
                if not df_geral_reqs.empty:
                    st.dataframe(formatar_dataframe_datas(df_geral_reqs), use_container_width=True, hide_index=True)
                    
                    st.markdown("---")
                    st.markdown("### ✅ Confirmar Entrega e Dar Baixa Definitiva no Estoque da Unidade")
                    conn = get_db_connection()
                    df_prontos = pd.read_sql(f"""
                        SELECT r.id_pedido, p.descricao, r.qtd_pedida, r.qtd_enviada_estoque, r.validade_sugerida
                        FROM requisicoes_loja r
                        JOIN produtos p ON r.codigo_produto = p.codigo
                        WHERE r.loja = '{loja_atual}' AND r.status = 'Pronto para Check-list'
                    """, conn)
                    conn.close()
                    
                    if not df_prontos.empty:
                        with st.form("form_checklist_gerente"):
                            id_ped_sel = st.selectbox("Selecione o ID do Item Separado", df_prontos['id_pedido'].tolist())
                            item_info = df_prontos[df_prontos['id_pedido'] == id_ped_sel].iloc[0]
                            
                            st.info(f"📋 **Item:** {item_info['descricao']} | 📥 **Solicitado:** {item_info['qtd_pedida']} | 📦 **Separado:** {item_info['qtd_enviada_estoque']}")
                            
                            validade_original_est = str(item_info['validade_sugerida'])
                            validade_gerente_edit = st.text_input("Validade conferida (Editável)", value=validade_original_est)

                            col_c1, col_c2 = st.columns(2)
                            with col_c1:
                                qtd_entregue = st.number_input("Quantidade Entregue / Consumida", min_value=0.0, value=float(item_info['qtd_enviada_estoque']), step=1.0)
                            with col_c2:
                                responsavel_baixa = st.text_input("Seu Nome (Responsável pela Validação)")
                            
                            btn_finalizar_chk = st.form_submit_button("✔️ Confirmar Entrega e Subtrair do Estoque", use_container_width=True)
                            
                            if btn_finalizar_chk:
                                if not responsavel_baixa.strip():
                                    st.warning("Informe o seu nome.")
                                else:
                                    data_hora = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                                    conn = get_db_connection()
                                    try:
                                        cursor = conn.cursor()
                                        cursor.execute("SELECT codigo_produto FROM requisicoes_loja WHERE id_pedido = ?", (id_ped_sel,))
                                        res_item = cursor.fetchone()
                                        cod_p = res_item[0]

                                        cursor.execute("UPDATE requisicoes_loja SET qtd_entregue = ?, validade_alterada_gerente = ?, estoque_responsavel = ?, status = 'Concluído' WHERE id_pedido = ?", 
                                                       (qtd_entregue, validade_gerente_edit.strip(), responsavel_baixa.strip(), id_ped_sel))
                                        
                                        # Executa a baixa permitindo saldo negativo na validade informada/editada
                                        data_val_baixa = validade_gerente_edit.strip() if validade_gerente_edit.strip() else datetime.now().strftime("%Y-%m-%d")
                                        descontar_estoque_fifo_com_negativo(cursor, cod_p, loja_atual, qtd_entregue, data_val_baixa)
                                            
                                        detalhe_log = f"Check-list item #{id_ped_sel} Concluído por {responsavel_baixa.strip()} | Baixa de {qtd_entregue} unidades em {loja_atual}. Validade conferida: {validade_gerente_edit}."
                                        cursor.execute("INSERT INTO logs_sistema (data, usuario, loja, tipo_acao, detalhes) VALUES (?, ?, ?, ?, ?)",
                                                       (data_hora, st.session_state.usuario, loja_atual, "BAIXA_CONSUMO", detalhe_log))
                                        conn.commit()
                                    finally:
                                        conn.close()
                                    st.success("Baixa realizada com sucesso no estoque da unidade!")
                                    st.rerun()
                    else:
                        st.info("Nenhum item aguardando check-list.")
                else:
                    st.info("Nenhuma requisição registrada.")

            with sub_chk2:
                st.markdown("#### 📜 Histórico de Check-lists Realizados (Validação de Usuário)")
                conn = get_db_connection()
                df_hist_chk = pd.read_sql(f"""
                    SELECT r.id_pedido, r.lote_id, r.data, r.solicitante, p.descricao, 
                           r.qtd_pedida, r.qtd_entregue, r.validade_alterada_gerente AS "Validade Validada", 
                           r.estoque_responsavel AS "Responsável Check-list"
                    FROM requisicoes_loja r
                    JOIN produtos p ON r.codigo_produto = p.codigo
                    WHERE r.loja = '{loja_atual}' AND r.status = 'Concluído'
                    ORDER BY r.id_pedido DESC
                """, conn)
                conn.close()

                if not df_hist_chk.empty:
                    st.dataframe(formatar_dataframe_datas(df_hist_chk), use_container_width=True, hide_index=True)
                else:
                    st.info("Nenhum check-list concluído no histórico desta unidade.")

        with tab_inv:
            col_t1, col_t2 = st.columns([5, 1])
            with col_t1:
                st.markdown("### 📊 Inventário Físico & Histórico")
            with col_t2:
                if st.button("🔄 Atualizar", key="ref_g_inv"):
                    st.rerun()

            sub_inv1, sub_inv2 = st.tabs(["Realizar Nova Contagem", "Histórico de Inventários Salvos"])
            
            with sub_inv1:
                st.markdown("#### Nova Contagem de Estoque")
                conn = get_db_connection()
                df_produtos_inv = pd.read_sql("SELECT codigo, descricao FROM produtos", conn)
                conn.close()

                termo_busca = st.text_input("🔍 Buscar item por nome ou código para contagem:")
                if termo_busca.strip():
                    df_produtos_inv = df_produtos_inv[df_produtos_inv['descricao'].str.contains(termo_busca, case=False, na=False) | df_produtos_inv['codigo'].str.contains(termo_busca, case=False, na=False)]

                if not df_produtos_inv.empty:
                    with st.form("form_inventario_gerente"):
                        contagens_usuario = {}
                        st.info("💡 Todos os itens iniciam com contagem 0.00 para digitação limpa.")
                        for idx, row in df_produtos_inv.iterrows():
                            col_i1, col_i2, col_i3 = st.columns([1, 3, 2])
                            with col_i1:
                                st.text(row['codigo'])
                            with col_i2:
                                st.text(row['descricao'])
                            with col_i3:
                                contagens_usuario[row['codigo']] = st.number_input(f"Contagem {row['codigo']}", min_value=0.0, value=0.0, step=1.0, key=f"inv_{row['codigo']}", label_visibility="collapsed")
                            st.markdown("---")

                        btn_salvar_contagem = st.form_submit_button("💾 Salvar Contagem e Ajustar Estoque da Unidade", use_container_width=True)
                    
                    if btn_salvar_contagem:
                        dados_csv = []
                        data_hora = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                        
                        conn = get_db_connection()
                        try:
                            cursor = conn.cursor()
                            for idx, row in df_produtos_inv.iterrows():
                                c_real = contagens_usuario[row['codigo']]
                                dados_csv.append({
                                    "cód": row['codigo'],
                                    "nome": row['descricao'],
                                    "contagem": c_real
                                })
                                
                                cursor.execute("DELETE FROM estoque_lotes WHERE loja = ? AND codigo = ?", (loja_atual, row['codigo']))
                                if c_real > 0:
                                    cursor.execute("INSERT INTO estoque_lotes (codigo, loja, quantidade, validade) VALUES (?, ?, ?, ?)",
                                                   (row['codigo'], loja_atual, c_real, (datetime.now() + timedelta(days=180)).strftime("%Y-%m-%d")))
                            
                            df_resultado_inv = pd.DataFrame(dados_csv)
                            csv_string = df_resultado_inv.to_csv(index=False, sep=';', encoding='utf-8-sig')
                            
                            cursor.execute("INSERT INTO inventarios_salvos (data, loja, responsavel, dados_csv) VALUES (?, ?, ?, ?)",
                                           (data_hora, loja_atual, st.session_state.usuario, csv_string))
                            cursor.execute("INSERT INTO logs_sistema (data, usuario, loja, tipo_acao, detalhes) VALUES (?, ?, ?, ?, ?)",
                                           (data_hora, st.session_state.usuario, loja_atual, "INVENTARIO_AJUSTE", f"Inventário físico realizado e estoque da loja {loja_atual} ajustado."))
                            conn.commit()
                        finally:
                            conn.close()
                        
                        st.success("Contagem salva e estoque da unidade ajustado com sucesso! Acesse a aba 'Histórico' para baixar o CSV.")
                else:
                    st.info("Nenhum produto encontrado.")

            with sub_inv2:
                st.markdown("#### 📂 Histórico de Inventários Realizados")
                conn = get_db_connection()
                df_hist_inv = pd.read_sql(f"SELECT id_inventario, data, responsavel FROM inventarios_salvos WHERE loja = '{loja_atual}' ORDER BY id_inventario DESC", conn)
                conn.close()

                if not df_hist_inv.empty:
                    st.dataframe(formatar_dataframe_datas(df_hist_inv), use_container_width=True, hide_index=True)
                    
                    inv_id_sel = st.selectbox("Selecione o ID do Inventário para Baixar o CSV", df_hist_inv['id_inventario'].tolist())
                    if inv_id_sel:
                        conn = get_db_connection()
                        cursor = conn.cursor()
                        cursor.execute("SELECT dados_csv, data FROM inventarios_salvos WHERE id_inventario = ?", (inv_id_sel,))
                        res_inv = cursor.fetchone()
                        conn.close()
                        
                        if res_inv:
                            csv_conteudo = res_inv[0]
                            data_inv = res_inv[1].replace(':', '-').replace(' ', '_')
                            st.download_button(
                                label="📥 Baixar Arquivo CSV deste Inventário (cód;nome;contagem)",
                                data=csv_conteudo,
                                file_name=f"inventario_{loja_atual.lower().replace(' ', '_')}_{data_inv}.csv",
                                mime="text/csv",
                                use_container_width=True
                            )
                else:
                    st.info("Nenhum inventário salvo no histórico desta unidade.")

        with tab_alertas:
            col_t1, col_t2 = st.columns([5, 1])
            with col_t1:
                st.markdown("### 🚨 Painel de Alertas de Validade (Próximos 7 Dias) & Estoque Mínimo")
            with col_t2:
                if st.button("🔄 Atualizar", key="ref_g_al"):
                    st.rerun()

            conn = get_db_connection()
            limite_aviso = (datetime.now() + timedelta(days=7)).strftime("%Y-%m-%d")
            
            df_validades_loja = pd.read_sql(f"""
                SELECT l.codigo, p.descricao, SUM(l.quantidade) as total_qtd, l.validade
                FROM estoque_lotes l
                JOIN produtos p ON l.codigo = p.codigo
                WHERE l.loja = '{loja_atual}'
                GROUP BY l.codigo, l.validade
                HAVING total_qtd > 0 AND l.validade <= '{limite_aviso}'
            """, conn)

            df_abaixo_loja = pd.read_sql(f"""
                SELECT p.codigo, p.descricao, COALESCE(SUM(e.quantidade), 0) as qtd_atual, p.estoque_minimo
                FROM produtos p
                LEFT JOIN estoque_lotes e ON p.codigo = e.codigo AND e.loja = '{loja_atual}'
                GROUP BY p.codigo
                HAVING qtd_atual < p.estoque_minimo
            """, conn)
            conn.close()

            col_ga1, col_ga2 = st.columns(2)
            with col_ga1:
                st.markdown("#### ⏳ Vencimento Próximo (7 dias)")
                if not df_validades_loja.empty:
                    st.dataframe(formatar_dataframe_datas(df_validades_loja), use_container_width=True, hide_index=True)
                else:
                    st.success("Nenhum item próximo ao vencimento.")
            with col_ga2:
                st.markdown("#### ⚠️ Estoque Abaixo do Mínimo")
                if not df_abaixo_loja.empty:
                    st.dataframe(df_abaixo_loja, use_container_width=True, hide_index=True)
                else:
                    st.success("Nenhum item abaixo do mínimo.")

    # --- 2. MÓDULO: ESTOQUISTA / ESTOQUISTA CHEFE ---
    elif perfil_atual in ["Estoquista", "Estoquista Chefe"]:
        st.markdown(f"### ⚙️ Painel de Operações da Unidade — {loja_atual}")
        
        if perfil_atual == "Estoquista Chefe":
            tab_conf, tab_est, tab_xml, tab_manual, tab_baixa, tab_alertas_ch = st.tabs([
                "📥 Pedidos da Unidade", "📋 Estoque Atual", "📥 Importar XML (Entrada)", "✍️ Entrada Manual", "⚠️ Baixa com Motivo", "🚨 Alertas & Vencimentos"
            ])
        else:
            tab_conf, tab_est, tab_xml, tab_manual, tab_alertas_ch = st.tabs([
                "📥 Pedidos da Unidade", "📋 Estoque Atual", "📥 Importar XML (Entrada)", "✍️ Entrada Manual", "🚨 Alertas & Vencimentos"
            ])

        with tab_conf:
            sub_c1, sub_c2 = st.tabs(["📦 Pedidos Pendentes (Separar)", "🖨️ Histórico de Separações & PDFs por Lote"])

            with sub_c1:
                col_t1, col_t2 = st.columns([5, 1])
                with col_t1:
                    st.markdown("### 📋 Avaliação e Separação de Pedidos Internos")
                with col_t2:
                    if st.button("🔄 Atualizar", key="ref_e_conf"):
                        st.rerun()

                conn = get_db_connection()
                df_reqs_estoque = pd.read_sql(f"""
                    SELECT r.id_pedido, r.lote_id, r.data, r.solicitante, p.codigo, p.descricao, r.qtd_pedida, r.status
                    FROM requisicoes_loja r
                    JOIN produtos p ON r.codigo_produto = p.codigo
                    WHERE r.loja = '{loja_atual}' AND r.status = 'Aguardando Conferência'
                """, conn)
                conn.close()

                if not df_reqs_estoque.empty:
                    st.dataframe(formatar_dataframe_datas(df_reqs_estoque), use_container_width=True, hide_index=True)
                    
                    with st.form("form_conferencia_estoquista"):
                        id_conf = st.selectbox("Selecione o ID do Pedido para Separar", df_reqs_estoque['id_pedido'].tolist())
                        item_req = df_reqs_estoque[df_reqs_estoque['id_pedido'] == id_conf].iloc[0]
                        
                        st.info(f"📋 **Item:** {item_req['descricao']} | 📥 **Solicitado:** {item_req['qtd_pedida']}")
                        
                        conn_lotes = get_db_connection()
                        df_lotes_disp = pd.read_sql(f"""
                            SELECT validade, SUM(quantidade) as qtd 
                            FROM estoque_lotes 
                            WHERE codigo = '{item_req['codigo']}' AND loja = '{loja_atual}' 
                            GROUP BY validade 
                            HAVING qtd > 0 
                            ORDER BY validade ASC
                        """, conn_lotes)
                        conn_lotes.close()

                        st.markdown("#### 📦 Selecione as quantidades a separar por Lote (Validade):")
                        entradas_lotes = {}
                        if not df_lotes_disp.empty:
                            for i, row_lote in df_lotes_disp.iterrows():
                                val_str = row_lote['validade']
                                qtd_disp = row_lote['qtd']
                                val_br = formatar_data_br(val_str)
                                entradas_lotes[val_str] = st.number_input(f"Lote: {val_br} (Disp: {qtd_disp})", min_value=0.0, value=0.0, step=1.0, key=f"conf_lote_{i}")
                        else:
                            st.info("ℹ️ Nenhum lote com saldo positivo encontrado. Você pode informar a quantidade total e a validade abaixo (o saldo ficará negativo).")

                        st.markdown("---")
                        st.markdown("**Outra validade ou Quantidade Excedente (Permite Saldo Negativo):**")
                        col_e1, col_e2 = st.columns(2)
                        with col_e1:
                            qtd_outra = st.number_input("Qtd de Outra Validade / Excedente", min_value=0.0, value=0.0, step=1.0)
                        with col_e2:
                            val_outra = st.date_input("Data da Validade", value=None, format="DD/MM/YYYY")

                        motivo_div = st.text_input("Motivo de Divergência (Obrigatório se Total Separado ≠ Solicitado)")
                        
                        btn_salvar_conf = st.form_submit_button("📤 Confirmar Separação", use_container_width=True)
                        
                        if btn_salvar_conf:
                            qtd_total_enviada = sum(entradas_lotes.values()) + qtd_outra
                            
                            partes_val = []
                            for v_str, q in entradas_lotes.items():
                                if q > 0:
                                    partes_val.append(f"{q}x ({formatar_data_br(v_str)})")
                            if qtd_outra > 0 and val_outra:
                                partes_val.append(f"{qtd_outra}x ({val_outra.strftime('%d/%m/%Y')})")
                            elif qtd_outra > 0 and not val_outra:
                                partes_val.append(f"{qtd_outra}x (Data Atual)")
                            
                            val_sug_final = " | ".join(partes_val)

                            if qtd_total_enviada == 0:
                                st.warning("⚠️ Você precisa separar pelo menos uma unidade maior que zero!")
                            elif qtd_outra > 0 and val_outra is None:
                                # Se inseriu quantidade extra mas não colocou data, assume a data atual por segurança
                                pass
                            
                            if qtd_total_enviada != float(item_req['qtd_pedida']) and not motivo_div.strip():
                                st.warning("⚠️ Como a quantidade separada é diferente da solicitada, o motivo de divergência é obrigatório!")
                            else:
                                data_hora = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                                
                                conn = get_db_connection()
                                try:
                                    cursor = conn.cursor()
                                    cursor.execute("""UPDATE requisicoes_loja 
                                                      SET qtd_enviada_estoque = ?, motivo_divergencia = ?, validade_sugerida = ?, status = 'Pronto para Check-list' 
                                                      WHERE id_pedido = ?""",
                                                   (qtd_total_enviada, motivo_div.strip(), val_sug_final, id_conf))
                                    
                                    cursor.execute("INSERT INTO logs_sistema (data, usuario, loja, tipo_acao, detalhes) VALUES (?, ?, ?, ?, ?)",
                                                   (data_hora, st.session_state.usuario, loja_atual, "SEPARACAO_PEDIDO", f"Separação item #{id_conf}: Qtd total={qtd_total_enviada}"))
                                    conn.commit()
                                finally:
                                    conn.close()
                                st.success("Separação confirmada com sucesso! O pedido foi liberado para o check-list final da unidade.")
                                st.rerun()
                else:
                    st.info("Nenhum pedido pendente no momento.")

            with sub_c2:
                st.markdown("### 🖨️ Histórico de Lotes Finalizados & Download em PDF Consolidado")
                conn = get_db_connection()
                df_hist_lotes = pd.read_sql(f"""
                    SELECT DISTINCT lote_id, data, solicitante, status
                    FROM requisicoes_loja
                    WHERE loja = '{loja_atual}' AND status != 'Aguardando Conferência'
                    ORDER BY id_pedido DESC
                """, conn)
                conn.close()

                if not df_hist_lotes.empty:
                    st.dataframe(formatar_dataframe_datas(df_hist_lotes), use_container_width=True, hide_index=True)
                    
                    lote_sel_pdf = st.selectbox("Selecione o Lote ID para Baixar o PDF Consolidado", df_hist_lotes['lote_id'].tolist(), key="sel_hist_pdf_lote")
                    if lote_sel_pdf:
                        pdf_buffer = gerar_pdf_lote_conferencia(lote_sel_pdf, loja_atual)
                        st.download_button(
                            label="📥 Baixar Relatório do Lote em PDF",
                            data=pdf_buffer,
                            file_name=f"relatorio_lote_{lote_sel_pdf}.pdf",
                            mime="application/pdf",
                            use_container_width=True
                        )
                else:
                    st.info("Nenhum lote finalizado no histórico ainda.")

        with tab_est:
            col_t1, col_t2 = st.columns([5, 1])
            with col_t1:
                st.markdown("### 📦 Saldo Atual da Unidade")
            with col_t2:
                if st.button("🔄 Atualizar", key="ref_e_est"):
                    st.rerun()

            conn = get_db_connection()
            df_estoque = pd.read_sql(f"""
                SELECT p.codigo, p.codigo_barras, p.codigo_fornecedor, p.descricao, p.categoria, p.unidade, COALESCE(SUM(e.quantidade), 0) as quantidade_total, p.estoque_minimo, p.custo
                FROM produtos p
                LEFT JOIN estoque_lotes e ON p.codigo = e.codigo AND e.loja = '{loja_atual}'
                GROUP BY p.codigo
            """, conn)
            conn.close()
            
            if not df_estoque.empty:
                st.dataframe(df_estoque[['codigo', 'codigo_barras', 'codigo_fornecedor', 'descricao', 'categoria', 'quantidade_total', 'unidade', 'estoque_minimo', 'custo']], use_container_width=True, hide_index=True)
                
                st.markdown("---")
                st.markdown("#### 🔍 Detalhamento de Saldos por Validade (Lotes / Permite Negativos)")
                prod_detalhe = st.selectbox("Selecione um produto para ver os lotes e validades", df_estoque['descricao'].tolist())
                cod_sel = df_estoque.loc[df_estoque['descricao'] == prod_detalhe, 'codigo'].values[0]
                
                conn = get_db_connection()
                # Exibe saldos consolidados por validade (mostrando negativos caso existam e ocultando apenas os realmente zerados)
                df_lotes_prod = pd.read_sql(f"""
                    SELECT validade as Validade, SUM(quantidade) as Quantidade
                    FROM estoque_lotes 
                    WHERE codigo = '{cod_sel}' AND loja = '{loja_atual}' 
                    GROUP BY validade 
                    HAVING Quantidade != 0 
                    ORDER BY validade ASC
                """, conn)
                conn.close()
                
                if not df_lotes_prod.empty:
                    st.dataframe(formatar_dataframe_datas(df_lotes_prod), use_container_width=True, hide_index=True)
                else:
                    st.info("Nenhum lote com saldo registrado para este item.")
            else:
                st.info("Nenhum produto cadastrado.")
            
        with tab_xml:
            st.markdown("### 📥 Importação de NF-e (Quantidade Fixa da Nota & Unidade Física Editável)")
            xml_file = st.file_uploader("Arquivo XML da Nota Fiscal", type=["xml"], key="upload_xml_nota")
            
            if xml_file is not None:
                try:
                    tree = ET.parse(xml_file)
                    root = tree.getroot()
                    ns = {'nfe': 'http://www.portalfiscal.inf.br/nfe'}
                    
                    inf_nfe = root.find('.//nfe:infNFe', ns)
                    chave_nfe = inf_nfe.get('Id', '').replace('NFe', '') if inf_nfe is not None else ""
                    
                    if not chave_nfe:
                        nNF_el = root.find('.//nfe:ide/nfe:nNF', ns)
                        chave_nfe = nNF_el.text if nNF_el is not None else "XML-GENERICO"

                    conn = get_db_connection()
                    cursor = conn.cursor()
                    cursor.execute("SELECT chave_nfe FROM nfs_importadas WHERE chave_nfe = ?", (chave_nfe,))
                    ja_importada = cursor.fetchone()
                    conn.close()

                    if ja_importada:
                        st.error(f"❌ Esta Nota Fiscal (Chave/Número: {chave_nfe}) já foi importada anteriormente no sistema! A importação foi bloqueada.")
                    else:
                        det_list = root.findall('.//nfe:det', ns)
                        itens_nf = []
                        for det in det_list:
                            prod = det.find('nfe:prod', ns)
                            c_prod = prod.find('nfe:cProd', ns).text if prod.find('nfe:cProd', ns) is not None else "SEM-COD"
                            x_prod = prod.find('nfe:xProd', ns).text if prod.find('nfe:xProd', ns) is not None else "Produto sem descrição"
                            q_com = float(prod.find('nfe:qCom', ns).text) if prod.find('nfe:qCom', ns) is not None else 0.0
                            v_un = float(prod.find('nfe:vUnCom', ns).text) if prod.find('nfe:vUnCom', ns) is not None else 0.0
                            u_com_xml = prod.find('nfe:uCom', ns).text if prod.find('nfe:uCom', ns) is not None else "un"
                            itens_nf.append({"Código": c_prod, "Descrição": x_prod, "Qtd": q_com, "UnidadeXML": u_com_xml, "Custo": v_un})
                        
                        st.markdown(f"**NF-e Identificada:** `{chave_nfe}` | **Total de Itens:** `{len(itens_nf)}`")
                        st.markdown("---")
                        
                        with st.form("form_confirma_xml"):
                            validades_digitadas = {}
                            unidades_fisicas = {}
                            
                            for i, item in enumerate(itens_nf):
                                st.markdown(f"**Item {i+1}: {item['Descrição']}** (Cód Forn: `{item['Código']}`)")
                                col_x1, col_x2, col_x3, col_x4 = st.columns(4)
                                with col_x1:
                                    st.markdown(f"**Qtd Nota:** `{item['Qtd']}`")
                                with col_x2:
                                    unidades_fisicas[i] = st.text_input(f"Unidade Física", value=item['UnidadeXML'], key=f"un_{i}")
                                with col_x3:
                                    validades_digitadas[i] = st.date_input(f"Validade", key=f"v_{i}", value=None, format="DD/MM/YYYY")
                                with col_x4:
                                    st.write(f"Custo Unit: R$ {item['Custo']:.2f}")
                                st.markdown("---")
                            
                            btn_conf_xml = st.form_submit_button("Confirmar Entrada de Todos os Itens da NF-e", use_container_width=True)
                            
                            if btn_conf_xml:
                                data_hora = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                                conn = get_db_connection()
                                try:
                                    cursor = conn.cursor()
                                    
                                    for i, item in enumerate(itens_nf):
                                        v_escolhida = validades_digitadas[i]
                                        v_str = v_escolhida.strftime("%Y-%m-%d") if v_escolhida else (datetime.now() + timedelta(days=180)).strftime("%Y-%m-%d")
                                        q_real = item['Qtd']
                                        un_fisica = unidades_fisicas[i].strip() if unidades_fisicas[i].strip() else item['UnidadeXML']
                                        cod_forn = item["Código"]
                                        
                                        cursor.execute("SELECT codigo FROM produtos WHERE codigo_fornecedor = ?", (cod_forn,))
                                        prod_existente = cursor.fetchone()
                                        
                                        if prod_existente:
                                            cod_sistema = prod_existente[0]
                                            cursor.execute("UPDATE produtos SET unidade = ?, custo = ? WHERE codigo = ?", (un_fisica, item['Custo'], cod_sistema))
                                        else:
                                            cod_sistema = gerar_proximo_codigo_produto(cursor)
                                            cursor.execute("""INSERT INTO produtos (codigo, codigo_barras, codigo_fornecedor, descricao, categoria, unidade, custo, estoque_minimo) 
                                                              VALUES (?, '', ?, ?, 'Geral', ?, ?, 5.0)""",
                                                           (cod_sistema, cod_forn, item['Descrição'], un_fisica, item['Custo']))
                                        
                                        cursor.execute("INSERT INTO estoque_lotes (codigo, loja, quantidade, validade) VALUES (?, ?, ?, ?)",
                                                       (cod_sistema, loja_atual, q_real, v_str))
                                    
                                    cursor.execute("INSERT OR REPLACE INTO nfs_importadas (chave_nfe, data_importacao, loja) VALUES (?, ?, ?)",
                                                   (chave_nfe, data_hora, loja_atual))
                                    
                                    cursor.execute("INSERT INTO logs_sistema (data, usuario, loja, tipo_acao, detalhes) VALUES (?, ?, ?, ?, ?)",
                                                   (data_hora, st.session_state.usuario, loja_atual, "ENTRADA_XML", f"Entrada NF-e chave {chave_nfe} em {loja_atual} com {len(itens_nf)} itens."))
                                    
                                    conn.commit()
                                    st.success("🎉 Todos os itens da Nota Fiscal foram cadastrados e deram entrada no estoque com sucesso!")
                                except Exception as db_err:
                                    st.error(f"Erro durante gravação no banco: {db_err}")
                                finally:
                                    conn.close()
                except Exception as e:
                    st.error(f"❌ Erro ao processar o XML: {e}")

        with tab_manual:
            st.markdown("### ✍️ Entrada Manual de Estoque")
            conn = get_db_connection()
            df_prods_m = pd.read_sql("SELECT codigo, descricao, unidade FROM produtos", conn)
            conn.close()
            
            if not df_prods_m.empty:
                with st.form("form_entrada_manual"):
                    prod_sel_m = st.selectbox("Selecione o Produto", df_prods_m['descricao'].tolist())
                    unid_reg = df_prods_m.loc[df_prods_m['descricao'] == prod_sel_m, 'unidade'].values[0]
                    
                    col_m1, col_m2, col_m3 = st.columns(3)
                    with col_m1:
                        qtd_m = st.number_input(f"Quantidade ({unid_reg})", min_value=0.0, value=0.0, step=1.0)
                    with col_m2:
                        custo_m = st.number_input("Preço Unitário (R$)", min_value=0.0, value=0.0, step=0.01)
                    with col_m3:
                        val_m = st.date_input("Validade do Lote (Opcional)", value=None, format="DD/MM/YYYY")
                    
                    btn_ent_man = st.form_submit_button("Confirmar Entrada Manual", use_container_width=True)
                    if btn_ent_man:
                        if qtd_m <= 0 or custo_m <= 0:
                            st.warning("⚠️ Informe uma quantidade e preço unitário válidos.")
                        else:
                            cod_m = df_prods_m.loc[df_prods_m['descricao'] == prod_sel_m, 'codigo'].values[0]
                            data_hora = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                            v_str = val_m.strftime("%Y-%m-%d") if val_m else (datetime.now() + timedelta(days=180)).strftime("%Y-%m-%d")
                            
                            conn = get_db_connection()
                            try:
                                cursor = conn.cursor()
                                cursor.execute("INSERT INTO estoque_lotes (codigo, loja, quantidade, validade) VALUES (?, ?, ?, ?)",
                                               (cod_m, loja_atual, qtd_m, v_str))
                                cursor.execute("UPDATE produtos SET custo = ? WHERE codigo = ?", (custo_m, cod_m))
                                cursor.execute("INSERT INTO logs_sistema (data, usuario, loja, tipo_acao, detalhes) VALUES (?, ?, ?, ?, ?)",
                                               (data_hora, st.session_state.usuario, loja_atual, "ENTRADA_MANUAL", f"Entrada manual de {qtd_m} {unid_reg} em {loja_atual}."))
                                conn.commit()
                            finally:
                                conn.close()
                            st.success("Entrada manual registrada com sucesso!")

        if perfil_atual == "Estoquista Chefe":
            with tab_baixa:
                st.markdown("### ⚠️ Baixa Manual de Estoque (Avaria / Vencimento)")
                conn = get_db_connection()
                df_est_b = pd.read_sql(f"""
                    SELECT p.codigo, p.descricao, SUM(e.quantidade) as quantidade
                    FROM produtos p
                    JOIN estoque_lotes e ON p.codigo = e.codigo AND e.loja = '{loja_atual}'
                    GROUP BY p.codigo
                """, conn)
                conn.close()
                
                if not df_est_b.empty:
                    with st.form("form_baixa_estoque"):
                        prod_baixa = st.selectbox("Selecione o Produto", df_est_b['descricao'].tolist())
                        
                        col_b1, col_b2 = st.columns(2)
                        with col_b1:
                            qtd_baixa = st.number_input("Quantidade a Retirar", min_value=0.1, step=1.0)
                        with col_b2:
                            motivo_baixa = st.text_input("Motivo (Avaria, Vencimento, etc.)")
                        
                        btn_conf_baixa = st.form_submit_button("Confirmar Baixa", use_container_width=True)
                        if btn_conf_baixa:
                            if not motivo_baixa.strip():
                                st.warning("O motivo é obrigatório!")
                            else:
                                cod_b = df_est_b.loc[df_est_b['descricao'] == prod_baixa, 'codigo'].values[0]
                                data_hora = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                                conn = get_db_connection()
                                try:
                                    cursor = conn.cursor()
                                    descontar_estoque_fifo_com_negativo(cursor, cod_b, loja_atual, qtd_baixa, datetime.now().strftime("%Y-%m-%d"))
                                    cursor.execute("INSERT INTO logs_sistema (data, usuario, loja, tipo_acao, detalhes) VALUES (?, ?, ?, ?, ?)",
                                                   (data_hora, st.session_state.usuario, loja_atual, "BAIXA_ESTOQUE", f"Baixa de {qtd_baixa} em {loja_atual}. Motivo: {motivo_baixa}"))
                                    conn.commit()
                                finally:
                                    conn.close()
                                st.success("Baixa realizada com sucesso!")
                                st.rerun()
                else:
                    st.info("Nenhum produto cadastrado.")

        with tab_alertas_ch:
            col_t1, col_t2 = st.columns([5, 1])
            with col_t1:
                st.markdown("### 🚨 Alertas & Vencimentos (Unidade)")
            with col_t2:
                if st.button("🔄 Atualizar", key="ref_e_al"):
                    st.rerun()

            conn = get_db_connection()
            limite_aviso = (datetime.now() + timedelta(days=7)).strftime("%Y-%m-%d")
            
            df_validades_ch = pd.read_sql(f"""
                SELECT l.codigo, p.descricao, SUM(l.quantidade) as total_qtd, l.validade
                FROM estoque_lotes l
                JOIN produtos p ON l.codigo = p.codigo
                WHERE l.loja = '{loja_atual}'
                GROUP BY l.codigo, l.validade
                HAVING total_qtd > 0 AND l.validade <= '{limite_aviso}'
            """, conn)
            conn.close()

            if not df_validades_ch.empty:
                st.warning("⚠️ Os seguintes itens estão próximos da data de vencimento:")
                st.dataframe(formatar_dataframe_datas(df_validades_ch), use_container_width=True, hide_index=True)
            else:
                st.success("Tudo em ordem! Nenhum item próximo ao vencimento nesta unidade.")

    # --- 3. MÓDULO: ADMINISTRADOR GLOBAL ---
    elif perfil_atual == "Administrador":
        st.markdown("### 👑 Painel Executivo Global")
        
        tab_alertas, tab_lojas, tab_users, tab_prod, tab_conv, tab_editar_prod, tab_excel, tab_visao, tab_logs = st.tabs([
            "🚨 Alertas Globais",
            "🏢 Lojas", 
            "👥 Usuários", 
            "✏️ Cadastro Mestre", 
            "🔄 Conversão de Unidades",
            "🛠️ Editar Produtos",
            "📊 Excel",
            "🌐 Visão Geral",
            "📜 Logs Filtrados"
        ])

        with tab_alertas:
            col_t1, col_t2 = st.columns([5, 1])
            with col_t1:
                st.markdown("### 🚨 Painel de Executivos: Estoques Críticos e Vencimentos")
            with col_t2:
                if st.button("🔄 Atualizar", key="ref_adm_al"):
                    st.rerun()

            conn = get_db_connection()
            df_abaixo = pd.read_sql("""
                SELECT e.loja, p.codigo, p.descricao, COALESCE(SUM(e.quantidade), 0) as qtd_atual, p.estoque_minimo
                FROM estoque_lotes e
                JOIN produtos p ON e.codigo = p.codigo
                GROUP BY e.loja, p.codigo
                HAVING qtd_atual < p.estoque_minimo
            """, conn)

            limite_str = (datetime.now() + timedelta(days=7)).strftime("%Y-%m-%d")
            df_venc = pd.read_sql(f"""
                SELECT e.loja, e.codigo, p.descricao, SUM(e.quantidade) as total_qtd, e.validade
                FROM estoque_lotes e
                JOIN produtos p ON e.codigo = p.codigo
                GROUP BY e.loja, e.codigo, e.validade
                HAVING total_qtd > 0 AND e.validade <= '{limite_str}'
            """, conn)
            conn.close()

            col_al1, col_al2 = st.columns(2)
            with col_al1:
                st.markdown("#### ⚠️ Abaixo do Mínimo")
                if not df_abaixo.empty:
                    st.dataframe(df_abaixo, use_container_width=True, hide_index=True)
                else:
                    st.success("Nenhum item abaixo do mínimo.")

            with col_al2:
                st.markdown("#### ⏳ Vencimento Próximo (7 dias)")
                if not df_venc.empty:
                    st.dataframe(formatar_dataframe_datas(df_venc), use_container_width=True, hide_index=True)
                else:
                    st.success("Nenhum vencimento próximo.")
        
        with tab_lojas:
            st.markdown("### 🏢 Gerenciamento de Lojas (Adicionar, Alterar e Excluir)")
            sub_l1, sub_l2, sub_l3 = st.tabs(["Cadastrar Nova Loja", "Alterar Nome de Loja", "Excluir Loja"])
            
            with sub_l1:
                with st.form("form_nova_loja"):
                    nova_loja = st.text_input("Nome da Nova Loja")
                    cad_loja = st.form_submit_button("Cadastrar Loja", use_container_width=True)
                    if cad_loja and nova_loja.strip():
                        conn = get_db_connection()
                        try:
                            cursor = conn.cursor()
                            cursor.execute("INSERT INTO lojas VALUES (?)", (nova_loja.strip(),))
                            conn.commit()
                            st.success(f"Loja '{nova_loja}' cadastrada com sucesso!")
                            st.rerun()
                        except Exception as e:
                            st.error(f"Erro: {e}")
                        finally:
                            conn.close()

            with sub_l2:
                conn = get_db_connection()
                df_lojas_alt = pd.read_sql("SELECT * FROM lojas", conn)
                conn.close()
                if not df_lojas_alt.empty:
                    with st.form("form_alterar_loja"):
                        loja_antiga = st.selectbox("Selecione a Loja para Alterar", df_lojas_alt['nome_loja'].tolist())
                        novo_nome_loja = st.text_input("Novo Nome da Loja")
                        btn_alt_loja = st.form_submit_button("Salvar Alteração de Nome", use_container_width=True)
                        if btn_alt_loja and novo_nome_loja.strip():
                            conn = get_db_connection()
                            try:
                                cursor = conn.cursor()
                                cursor.execute("UPDATE lojas SET nome_loja = ? WHERE nome_loja = ?", (novo_nome_loja.strip(), loja_antiga))
                                cursor.execute("UPDATE usuarios SET loja = ? WHERE loja = ?", (novo_nome_loja.strip(), loja_antiga))
                                cursor.execute("UPDATE estoque_lotes SET loja = ? WHERE loja = ?", (novo_nome_loja.strip(), loja_antiga))
                                cursor.execute("UPDATE requisicoes_loja SET loja = ? WHERE loja = ?", (novo_nome_loja.strip(), loja_antiga))
                                conn.commit()
                                st.success(f"Loja alterada com sucesso para '{novo_nome_loja.strip()}'!")
                                st.rerun()
                            except Exception as e:
                                st.error(f"Erro ao alterar: {e}")
                            finally:
                                conn.close()
                else:
                    st.info("Nenhuma loja cadastrada.")

            with sub_l3:
                conn = get_db_connection()
                df_lojas = pd.read_sql("SELECT * FROM lojas", conn)
                conn.close()
                if not df_lojas.empty:
                    with st.form("form_excluir_loja"):
                        loja_para_excluir = st.selectbox("Selecione uma loja para excluir", df_lojas['nome_loja'].tolist())
                        btn_del_loja = st.form_submit_button("Excluir Loja Selecionada", use_container_width=True)
                        if btn_del_loja:
                            conn = get_db_connection()
                            try:
                                cursor = conn.cursor()
                                cursor.execute("DELETE FROM lojas WHERE nome_loja = ?", (loja_para_excluir,))
                                cursor.execute("DELETE FROM usuarios WHERE loja = ?", (loja_para_excluir,))
                                conn.commit()
                                st.success(f"Loja '{loja_para_excluir}' excluída com sucesso!")
                                st.rerun()
                            finally:
                                conn.close()
                else:
                    st.info("Nenhuma loja cadastrada.")
                    
            st.markdown("---")
            conn = get_db_connection()
            df_lojas_geral = pd.read_sql("SELECT * FROM lojas", conn)
            conn.close()
            st.dataframe(df_lojas_geral, use_container_width=True, hide_index=True)
                
        with tab_users:
            st.markdown("### 👥 Gerenciamento de Usuários")
            conn = get_db_connection()
            df_l = pd.read_sql("SELECT * FROM lojas", conn)
            conn.close()
            lista_lojas_cad = df_l['nome_loja'].tolist() if not df_l.empty else []
            lista_lojas_cad.insert(0, "Geral")
            
            with st.form("form_novo_usuario"):
                col_u1, col_u2 = st.columns(2)
                with col_u1:
                    u_nome = st.text_input("Usuário (Login)")
                    u_senha = st.text_input("Senha", type="password")
                with col_u2:
                    u_perfil = st.selectbox("Perfil de Acesso", ["Gerente", "Estoquista", "Estoquista Chefe", "Administrador"])
                    u_loja = st.selectbox("Loja Vinculada", lista_lojas_cad)
                
                cad_user = st.form_submit_button("Salvar / Alterar Usuário", use_container_width=True)
                if cad_user and u_nome.strip() and u_senha.strip():
                    conn = get_db_connection()
                    try:
                        cursor = conn.cursor()
                        cursor.execute("INSERT OR REPLACE INTO usuarios VALUES (?, ?, ?, ?)", (u_nome.strip(), u_senha, u_perfil, u_loja))
                        conn.commit()
                        st.success("Usuário salvo com sucesso!")
                        st.rerun()
                    finally:
                        conn.close()
                    
            st.markdown("---")
            conn = get_db_connection()
            df_users = pd.read_sql("SELECT * FROM usuarios", conn)
            conn.close()
            st.dataframe(df_users, use_container_width=True, hide_index=True)

        with tab_prod:
            st.markdown("### ✏️ Cadastro Mestre de Produtos")
            with st.form("form_cad_produto"):
                col_p1, col_p2, col_p3 = st.columns(3)
                with col_p1:
                    c_cod = st.text_input("Código Principal")
                    c_barras = st.text_input("Código de Barras (Opcional)")
                    c_forn = st.text_input("Código do Fornecedor (Opcional)")
                with col_p2:
                    c_desc = st.text_input("Descrição do Item")
                    c_cat = st.text_input("Categoria")
                with col_p3:
                    c_un = st.text_input("Unidade (Ex: un, Cx, Fd, kg, L)")
                    c_min = st.number_input("Estoque Mínimo", min_value=0.0, value=5.0, step=1.0)
                    c_custo = st.number_input("Custo (R$)", min_value=0.0)
                
                salvar_prod = st.form_submit_button("Cadastrar Produto Mestre", use_container_width=True)
                if salvar_prod and c_cod.strip():
                    conn = get_db_connection()
                    try:
                        cursor = conn.cursor()
                        cursor.execute("INSERT OR REPLACE INTO produtos (codigo, codigo_barras, codigo_fornecedor, descricao, categoria, unidade, custo, estoque_minimo) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                                       (c_cod.strip(), c_barras.strip(), c_forn.strip(), c_desc.strip(), c_cat.strip(), c_un.strip(), c_custo, c_min))
                        conn.commit()
                        st.success("Produto cadastrado com sucesso!")
                    finally:
                        conn.close()

        with tab_conv:
            st.markdown("### 🔄 Cadastro de Equivalência e Fatores de Conversão")
            st.markdown("*(Exemplo: 1 Caixa (Cx) equivale a 12 Fardos (Fd) ou 1 Fardo (Fd) equivale a 24 Unidades (un).)*")
            
            conn = get_db_connection()
            df_prods_conv = pd.read_sql("SELECT codigo, descricao FROM produtos", conn)
            conn.close()
            
            if not df_prods_conv.empty:
                with st.form("form_cad_conversao"):
                    p_sel_conv = st.selectbox("Selecione o Produto", df_prods_conv['descricao'].tolist())
                    
                    col_cv1, col_cv2, col_cv3 = st.columns(3)
                    with col_cv1:
                        un_origem = st.text_input("Unidade Origem (Ex: Cx)", value="Cx")
                    with col_cv2:
                        un_destino = st.text_input("Unidade Destino (Ex: Fd)", value="Fd")
                    with col_cv3:
                        fator = st.number_input("Fator de Equivalência (Quantos Destino cabem na Origem)", min_value=0.001, value=1.0, step=1.0)
                    
                    btn_salvar_conv = st.form_submit_button("Salvar Regra de Conversão", use_container_width=True)
                    if btn_salvar_conv:
                        cod_p_conv = df_prods_conv.loc[df_prods_conv['descricao'] == p_sel_conv, 'codigo'].values[0]
                        conn = get_db_connection()
                        try:
                            cursor = conn.cursor()
                            cursor.execute("INSERT INTO conversoes_unidades (codigo_produto, unidade_origem, unidade_destino, fator_conversao) VALUES (?, ?, ?, ?)",
                                           (cod_p_conv, un_origem.strip(), un_destino.strip(), fator))
                            conn.commit()
                            st.success("Regra de conversão cadastrada com sucesso!")
                        finally:
                            conn.close()
                
                st.markdown("---")
                st.markdown("#### 📋 Regras de Conversão Cadastradas")
                conn = get_db_connection()
                df_regras = pd.read_sql("""
                    SELECT c.id_conversao, p.descricao, c.unidade_origem, c.unidade_destino, c.fator_conversao
                    FROM conversoes_unidades c
                    JOIN produtos p ON c.codigo_produto = p.codigo
                """, conn)
                conn.close()
                if not df_regras.empty:
                    st.dataframe(df_regras, use_container_width=True, hide_index=True)
                else:
                    st.info("Nenhuma regra cadastrada.")
            else:
                st.info("Cadastre produtos mestre primeiro para definir conversões.")

        with tab_editar_prod:
            st.markdown("### 🛠️ Consulta e Edição de Produtos")
            conn = get_db_connection()
            df_mestre = pd.read_sql("SELECT * FROM produtos", conn)
            conn.close()
            
            if not df_mestre.empty:
                st.dataframe(df_mestre, use_container_width=True, hide_index=True)
                prod_para_editar = st.selectbox("Selecione para alterar", df_mestre['descricao'].tolist())
                item_atual = df_mestre[df_mestre['descricao'] == prod_para_editar].iloc[0]
                
                with st.form("form_edicao_produto"):
                    e_cod = st.text_input("Código", value=str(item_atual['codigo']))
                    e_barras = st.text_input("Código de Barras", value=str(item_atual['codigo_barras']) if pd.notna(item_atual['codigo_barras']) else "")
                    e_forn = st.text_input("Código do Fornecedor", value=str(item_atual['codigo_fornecedor']) if pd.notna(item_atual['codigo_fornecedor']) else "")
                    e_desc = st.text_input("Descrição", value=str(item_atual['descricao']))
                    e_cat = st.text_input("Categoria", value=str(item_atual['categoria']))
                    e_un = st.text_input("Unidade", value=str(item_atual['unidade']))
                    e_min = st.number_input("Estoque Mínimo", min_value=0.0, value=float(item_atual['estoque_minimo']), step=1.0)
                    e_custo = st.number_input("Custo (R$)", min_value=0.0, value=float(item_atual['custo']), step=0.01)
                    
                    btn_salvar_edicao = st.form_submit_button("Salvar Alterações", use_container_width=True)
                    if btn_salvar_edicao:
                        conn = get_db_connection()
                        try:
                            cursor = conn.cursor()
                            cursor.execute("""UPDATE produtos SET codigo = ?, codigo_barras = ?, codigo_fornecedor = ?, descricao = ?, categoria = ?, unidade = ?, custo = ?, estoque_minimo = ? WHERE codigo = ?""",
                                           (e_cod.strip(), e_barras.strip(), e_forn.strip(), e_desc.strip(), e_cat.strip(), e_un.strip(), e_custo, e_min, item_atual['codigo']))
                            conn.commit()
                            st.success("Atualizado com sucesso!")
                            st.rerun()
                        finally:
                            conn.close()
            else:
                st.info("Nenhum produto cadastrado.")
                        
        with tab_excel:
            st.markdown("### 📊 Importação / Exportação Excel")
            conn = get_db_connection()
            df_export = pd.read_sql("SELECT * FROM produtos", conn)
            conn.close()
            
            if not df_export.empty:
                buffer_excel = io.BytesIO()
                with pd.ExcelWriter(buffer_excel, engine='openpyxl') as writer:
                    df_export.to_excel(writer, index=False, sheet_name='Produtos')
                buffer_excel.seek(0)
                st.download_button("📥 Baixar Planilha Excel de Produtos", data=buffer_excel, file_name="produtos.xlsx", mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", use_container_width=True)
            
            uploaded_excel = st.file_uploader("Importar Planilha (.xlsx)", type=["xlsx"])
            if uploaded_excel is not None:
                df_imp = pd.read_excel(uploaded_excel)
                if st.button("Confirmar Importação em Massa", use_container_width=True):
                    conn = get_db_connection()
                    try:
                        cursor = conn.cursor()
                        for _, row in df_imp.iterrows():
                            cursor.execute("INSERT OR REPLACE INTO produtos (codigo, codigo_barras, codigo_fornecedor, descricao, categoria, unidade, custo, estoque_minimo) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                                           (str(row['codigo']), str(row.get('codigo_barras', '')), str(row.get('codigo_fornecedor', '')), str(row['descricao']), str(row['categoria']), str(row['unidade']), float(row['custo']), float(row.get('estoque_minimo', 5.0))))
                        conn.commit()
                        st.success("Importado com sucesso!")
                    finally:
                        conn.close()

        with tab_visao:
            col_t1, col_t2 = st.columns([5, 1])
            with col_t1:
                st.markdown("### 🌐 Visão Global de Estoques")
            with col_t2:
                if st.button("🔄 Atualizar", key="ref_adm_vis"):
                    st.rerun()

            conn = get_db_connection()
            df_l = pd.read_sql("SELECT * FROM lojas", conn)
            conn.close()
            lista_lojas_filtro = df_l['nome_loja'].tolist() if not df_l.empty else []
            lista_lojas_filtro.insert(0, "Todas as Lojas (Consolidado)")
            loja_escolhida_admin = st.selectbox("Filtrar por Unidade", lista_lojas_filtro)
            
            conn = get_db_connection()
            if loja_escolhida_admin == "Todas as Lojas (Consolidado)":
                df_geral = pd.read_sql("""
                    SELECT e.loja, p.codigo, p.codigo_barras, p.codigo_fornecedor, p.descricao, p.categoria, SUM(e.quantidade) as quantidade, p.unidade, p.estoque_minimo, p.custo, e.validade
                    FROM estoque_lotes e
                    JOIN produtos p ON e.codigo = p.codigo
                    GROUP BY e.loja, p.codigo, e.validade
                    HAVING quantidade != 0
                """, conn)
            else:
                df_geral = pd.read_sql(f"""
                    SELECT e.loja, p.codigo, p.codigo_barras, p.codigo_fornecedor, p.descricao, p.categoria, SUM(e.quantidade) as quantidade, p.unidade, p.estoque_minimo, p.custo, e.validade
                    FROM estoque_lotes e
                    JOIN produtos p ON e.codigo = p.codigo
                    WHERE e.loja = '{loja_escolhida_admin}'
                    GROUP BY p.codigo, e.validade
                    HAVING quantidade != 0
                """, conn)
            conn.close()
            
            if not df_geral.empty:
                st.dataframe(formatar_dataframe_datas(df_geral), use_container_width=True, hide_index=True)
            else:
                st.info("Nenhum estoque registrado.")

        with tab_logs:
            col_t1, col_t2 = st.columns([5, 1])
            with col_t1:
                st.markdown("### 📜 Log Completo de Auditoria com Filtros Avançados")
            with col_t2:
                if st.button("🔄 Atualizar", key="ref_adm_log"):
                    st.rerun()

            conn = get_db_connection()
            df_logs_full = pd.read_sql("SELECT * FROM logs_sistema", conn)
            
            col_f1, col_f2, col_f3 = st.columns(3)
            with col_f1:
                tipos_disponiveis = ["Todos"] + df_logs_full['tipo_acao'].unique().tolist() if not df_logs_full.empty else ["Todos"]
                filtro_tipo = st.selectbox("Filtrar por Tipo de Ação", tipos_disponiveis)
            with col_f2:
                lojas_disponiveis = ["Todas"] + df_logs_full['loja'].unique().tolist() if not df_logs_full.empty else ["Todas"]
                filtro_loja = st.selectbox("Filtrar por Loja", lojas_disponiveis)
            with col_f3:
                usuarios_disponiveis = ["Todos"] + df_logs_full['usuario'].unique().tolist() if not df_logs_full.empty else ["Todos"]
                filtro_usuario = st.selectbox("Filtrar por Login/Usuário", usuarios_disponiveis)

            query_log = "SELECT * FROM logs_sistema WHERE 1=1"
            params = []
            if filtro_tipo != "Todos":
                query_log += " AND tipo_acao = ?"
                params.append(filtro_tipo)
            if filtro_loja != "Todas":
                query_log += " AND loja = ?"
                params.append(filtro_loja)
            if filtro_usuario != "Todos":
                query_log += " AND usuario = ?"
                params.append(filtro_usuario)
            
            query_log += " ORDER BY id_log DESC"
            df_logs = pd.read_sql(query_log, conn, params=params)
            conn.close()
            
            if not df_logs.empty:
                st.dataframe(formatar_dataframe_datas(df_logs), use_container_width=True, hide_index=True)
                
                st.markdown("---")
                st.markdown("#### 🔍 Detalhes Ampliados do Log")
                log_id = st.selectbox("Inspecionar ID do Log", df_logs['id_log'].tolist())
                if log_id:
                    row_l = df_logs[df_logs['id_log'] == log_id].iloc[0]
                    st.info(f"**Data:** {formatar_data_br(row_l['data'])} | **Usuário:** {row_l['usuario']} | **Loja:** {row_l['loja']} | **Ação:** {row_l['tipo_acao']}")
                    st.success(row_l['detalhes'])
            else:
                st.info("Nenhum log encontrado com os filtros selecionados.")
