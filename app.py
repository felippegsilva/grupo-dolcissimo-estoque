import streamlit as st
import pandas as pd
import sqlite3
from datetime import datetime, timedelta
import io
import xml.etree.ElementTree as ET
import urllib.parse

# --- CONFIGURAÇÃO DA PÁGINA ---
st.set_page_config(
    page_title="Grupo Dolcissimo - Sistema de Estoque", 
    page_icon="📦", 
    layout="wide",
    initial_sidebar_state="expanded"
)

# --- CSS PERSONALIZADO PARA BELEZA VISUAL E ESTILIZAÇÃO ---
st.markdown("""
    <style>
        .main {
            background-color: #f8f9fa;
        }
        .stButton>button {
            border-radius: 8px;
            font-weight: bold;
            transition: 0.3s;
        }
        .stButton>button:hover {
            border-color: #25D366;
            color: #25D366;
        }
        div[data-testid="stMetricValue"] {
            font-size: 24px;
            color: #2c3e50;
        }
    </style>
""", unsafe_allow_html=True)

# --- CONFIGURAÇÃO DO BANCO DE DADOS ---
def init_db():
    conn = sqlite3.connect('sistema_estoque.db')
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
    conn.commit()

    cursor.execute("""CREATE TABLE IF NOT EXISTS produtos (
                        codigo TEXT PRIMARY KEY, 
                        descricao TEXT, 
                        categoria TEXT, 
                        unidade TEXT, 
                        custo REAL,
                        estoque_minimo REAL DEFAULT 5.0)""")

    cursor.execute("""CREATE TABLE IF NOT EXISTS estoque_lotes (
                        id_lote INTEGER PRIMARY KEY AUTOINCREMENT,
                        codigo TEXT,
                        loja TEXT,
                        quantidade REAL,
                        validade TEXT)""")

    cursor.execute("""CREATE TABLE IF NOT EXISTS requisicoes_loja (
                        id_pedido INTEGER PRIMARY KEY AUTOINCREMENT,
                        lote_id TEXT,
                        data TEXT,
                        loja TEXT,
                        solicitante TEXT,
                        codigo_produto TEXT,
                        qtd_pedida REAL,
                        qtd_estoque_conferencia REAL DEFAULT 0,
                        obs_estoquista TEXT DEFAULT '',
                        qtd_entregue REAL DEFAULT 0,
                        estoque_responsavel TEXT,
                        validade_informada TEXT,
                        status TEXT,
                        observacao TEXT)""")

    cursor.execute("""CREATE TABLE IF NOT EXISTS logs_sistema (
                        id_log INTEGER PRIMARY KEY AUTOINCREMENT,
                        data TEXT,
                        usuario TEXT,
                        loja TEXT,
                        tipo_acao TEXT,
                        detalhes TEXT)""")
    
    conn.commit()
    conn.close()

init_db()

# --- FUNÇÃO AUXILIAR DE DISPLAY DE UNIDADES ---
def formatar_unidades(qtd):
    if pd.isna(qtd):
        qtd = 0.0
    cxs = int(qtd // 24)
    rest_cx = qtd % 24
    fardos = int(rest_cx // 6)
    displays = rest_cx % 6
    return f"{int(qtd)} un (Caixas: {cxs} | Fardos: {fardos} | Disp: {displays})"

# --- TELA DE LOGIN ---
st.sidebar.title("🔐 Acesso ao Sistema")

if 'autenticado' not in st.session_state:
    st.session_state.autenticado = False
    st.session_state.usuario = ""
    st.session_state.perfil = ""
    st.session_state.loja = ""

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
                conn = sqlite3.connect('sistema_estoque.db')
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
            "📝 Carrinho de Requisição", 
            "✔️ Check-list Consolidado", 
            "📋 Inventário da Loja",
            "🚨 Alertas & Vencimentos"
        ])
        
        if 'carrinho_requisicao' not in st.session_state:
            st.session_state.carrinho_requisicao = []
        if 'carrinho_checklist' not in st.session_state:
            st.session_state.carrinho_checklist = []

        with tab_ped:
            st.markdown("### 🛒 Solicitação de Materiais")
            conn = sqlite3.connect('sistema_estoque.db')
            df_produtos = pd.read_sql(f"""
                SELECT p.codigo, p.descricao, p.categoria, p.unidade, COALESCE(SUM(e.quantidade), 0) as estoque_atual, p.estoque_minimo, p.custo
                FROM produtos p
                LEFT JOIN estoque_lotes e ON p.codigo = e.codigo AND e.loja = '{loja_atual}'
                GROUP BY p.codigo
            """, conn)
            conn.close()
            
            if not df_produtos.empty:
                df_produtos['estoque_formatado'] = df_produtos['estoque_atual'].apply(formatar_unidades)
                st.dataframe(
                    df_produtos[['codigo', 'descricao', 'categoria', 'estoque_formatado', 'estoque_minimo', 'custo']], 
                    use_container_width=True,
                    hide_index=True
                )
                
                with st.form("form_add_carrinho"):
                    col_a, col_b = st.columns([2, 1])
                    with col_a:
                        prod_sel = st.selectbox("Selecione o Produto", df_produtos['descricao'].tolist())
                        est_atual_item = df_produtos.loc[df_produtos['descricao'] == prod_sel, 'estoque_atual'].values[0]
                        st.info(f"ℹ️ Estoque atual desta unidade: **{formatar_unidades(est_atual_item)}**")
                    with col_b:
                        qtd_pedida = st.number_input("Quantidade Desejada", min_value=1.0, step=1.0)
                        
                    btn_add = st.form_submit_button("➕ Adicionar ao Carrinho", use_container_width=True)
                    
                    if btn_add:
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
                        
                        btn_enviar_lote = st.form_submit_button("🚀 Finalizar e Enviar Requisição Completa", use_container_width=True)
                        if btn_enviar_lote:
                            if not nome_resp.strip():
                                st.warning("Por favor, informe o seu nome.")
                            else:
                                data_hora = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                                lote_id = datetime.now().strftime("LOTE-%Y%m%d%H%M%S")
                                
                                conn = sqlite3.connect('sistema_estoque.db')
                                cursor = conn.cursor()
                                
                                itens_detalhes = []
                                for item in st.session_state.carrinho_requisicao:
                                    cursor.execute("""INSERT INTO requisicoes_loja (lote_id, data, loja, solicitante, codigo_produto, qtd_pedida, qtd_estoque_conferencia, obs_estoquista, qtd_entregue, estoque_responsavel, validade_informada, status, observacao) 
                                                      VALUES (?, ?, ?, ?, ?, ?, 0, '', 0, '', '', 'Pendente', ?)""",
                                                   (lote_id, data_hora, loja_atual, nome_resp, item['codigo'], item['quantidade'], obs_lote))
                                    itens_detalhes.append(f"{item['quantidade']}x {item['descricao']}")
                                
                                desc_log = f"Requisição Lote {lote_id} ({len(st.session_state.carrinho_requisicao)} itens). Solicitante: {nome_resp}."
                                cursor.execute("INSERT INTO logs_sistema (data, usuario, loja, tipo_acao, detalhes) VALUES (?, ?, ?, ?, ?)",
                                               (data_hora, st.session_state.usuario, loja_atual, "REQUISICAO_LOTE", desc_log))
                                
                                conn.commit()
                                conn.close()
                                
                                carrinho_temp = st.session_state.carrinho_requisicao.copy()
                                st.session_state.carrinho_requisicao = []
                                st.success("Requisição enviada com sucesso!")
                                
                                itens_str = "%0A".join([f"- {i['quantidade']}x {i['descricao']}" for i in carrinho_temp])
                                texto_zap = f"*GRUPO DOLCISSIMO - REQUISIÇÃO DE ESTOQUE*%0A" \
                                            f"--------------------------------------------------%0A" \
                                            f"🏢 *Unidade:* {loja_atual}%0A" \
                                            f"📋 *Lote:* {lote_id}%0A" \
                                            f"📅 *Data:* {data_hora}%0A" \
                                            f"👤 *Solicitante:* {nome_resp}%0A" \
                                            f"📦 *Itens:*%0A{itens_str}%0A" \
                                            f"💬 *Obs:* {obs_lote if obs_lote else 'Nenhuma'}"
                                
                                link_whatsapp = f"https://wa.me/?text={texto_zap}"
                                st.markdown(f'<a href="{link_whatsapp}" target="_blank"><button style="background-color:#25D366; color:white; padding:12px 24px; border:none; border-radius:8px; font-weight:bold; font-size:16px; cursor:pointer; width:100%;">📲 Enviar Pedido Completo via WhatsApp</button></a>', unsafe_allow_html=True)
                else:
                    st.info("O carrinho de requisição está vazio.")
            else:
                st.info("Nenhum produto cadastrado.")
                
        with tab_check:
            st.markdown("### 🔄 Fluxo Consolidado (Requisição ➔ Verificação Estoquista ➔ Chegada)")
            
            conn = sqlite3.connect('sistema_estoque.db')
            df_geral_reqs = pd.read_sql(f"""
                SELECT r.id_pedido, r.lote_id, r.data, r.solicitante, p.descricao, r.qtd_pedida, 
                       r.qtd_estoque_conferencia, r.obs_estoquista, r.qtd_entregue, r.estoque_responsavel, r.status
                FROM requisicoes_loja r
                JOIN produtos p ON r.codigo_produto = p.codigo
                WHERE r.loja = '{loja_atual}'
                ORDER BY r.id_pedido DESC
            """, conn)
            conn.close()
            
            if not df_geral_reqs.empty:
                st.dataframe(df_geral_reqs, use_container_width=True, hide_index=True)
                
                st.markdown("---")
                st.markdown("### ✅ Registrar Chegada de Itens Pendentes")
                df_pendentes = df_geral_reqs[df_geral_reqs['status'] == 'Pendente']
                
                if not df_pendentes.empty:
                    with st.form("form_checklist_gerente"):
                        id_ped_sel = st.selectbox("Selecione o ID do Item", df_pendentes['id_pedido'].tolist())
                        item_info = df_pendentes[df_pendentes['id_pedido'] == id_ped_sel].iloc[0]
                        
                        col_c1, col_c2, col_c3 = st.columns(3)
                        with col_c1:
                            qtd_conferida = st.number_input("Qtd Recebida na Loja", min_value=0.0, value=float(item_info['qtd_pedida']), step=1.0)
                        with col_c2:
                            val_item = st.date_input("Validade do Lote")
                        with col_c3:
                            estoquista_entregou = st.text_input("Estoquista Entregador")
                        
                        btn_finalizar_chk = st.form_submit_button("✔️ Confirmar Recebimento e Atualizar Estoque", use_container_width=True)
                        
                        if btn_finalizar_chk:
                            if not estoquista_entregou.strip():
                                st.warning("Informe o nome do estoquista.")
                            else:
                                data_hora = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                                conn = sqlite3.connect('sistema_estoque.db')
                                cursor = conn.cursor()
                                
                                cursor.execute("SELECT codigo_produto FROM requisicoes_loja WHERE id_pedido = ?", (id_ped_sel,))
                                cod_p = cursor.fetchone()[0]
                                v_str = val_item.strftime("%Y-%m-%d")
                                
                                cursor.execute("UPDATE requisicoes_loja SET qtd_entregue = ?, estoque_responsavel = ?, validade_informada = ?, status = 'Concluído' WHERE id_pedido = ?", 
                                               (qtd_conferida, estoquista_entregou.strip(), v_str, id_ped_sel))
                                
                                cursor.execute("INSERT INTO estoque_lotes (codigo, loja, quantidade, validade) VALUES (?, ?, ?, ?)", 
                                               (cod_p, loja_atual, qtd_conferida, v_str))
                                    
                                detalhe_log = f"Check-list item #{id_ped_sel} Concluído | Entregue por: {estoquista_entregou.strip()} | Qtd: {qtd_conferida} (Val: {v_str})"
                                cursor.execute("INSERT INTO logs_sistema (data, usuario, loja, tipo_acao, detalhes) VALUES (?, ?, ?, ?, ?)",
                                               (data_hora, st.session_state.usuario, loja_atual, "CHECKLIST_ITEM", detalhe_log))
                                
                                conn.commit()
                                conn.close()
                                st.success("Check-list concluído com sucesso!")
                                st.rerun()
                else:
                    st.info("Nenhum item pendente de chegada.")
            else:
                st.info("Nenhuma requisição registrada.")

        with tab_inv:
            st.markdown("### 📊 Inventário Físico da Loja")
            conn = sqlite3.connect('sistema_estoque.db')
            df_inv = pd.read_sql(f"""
                SELECT p.codigo as "cód", p.descricao as "nome", COALESCE(SUM(e.quantidade), 0) as "quant atual"
                FROM produtos p
                LEFT JOIN estoque_lotes e ON p.codigo = e.codigo AND e.loja = '{loja_atual}'
                GROUP BY p.codigo, p.descricao
            """, conn)
            conn.close()

            if not df_inv.empty:
                st.dataframe(df_inv, use_container_width=True, hide_index=True)
                csv_data = df_inv.to_csv(index=False, sep=';', encoding='utf-8-sig')
                st.download_button(
                    label="📥 Baixar Inventário em Formato CSV",
                    data=csv_data,
                    file_name=f"inventario_{loja_atual.lower().replace(' ', '_')}.csv",
                    mime="text/csv",
                    use_container_width=True
                )
            else:
                st.info("Nenhum produto cadastrado.")

        with tab_alertas:
            st.markdown("### 🚨 Painel de Alertas de Validade (Próximos 7 Dias)")
            conn = sqlite3.connect('sistema_estoque.db')
            limite_aviso = (datetime.now() + timedelta(days=7)).strftime("%Y-%m-%d")
            
            df_validades_loja = pd.read_sql(f"""
                SELECT l.codigo, p.descricao, SUM(l.quantidade) as total_qtd, l.validade
                FROM estoque_lotes l
                JOIN produtos p ON l.codigo = p.codigo
                WHERE l.loja = '{loja_atual}' AND l.quantidade > 0
                GROUP BY l.codigo, l.validade
                HAVING l.validade <= '{limite_aviso}'
            """, conn)
            conn.close()

            if not df_validades_loja.empty:
                st.warning("⚠️ Os seguintes itens estão próximos da data de vencimento:")
                st.dataframe(df_validades_loja, use_container_width=True, hide_index=True)
            else:
                st.success("Tudo em ordem! Nenhum item próximo ao vencimento nesta unidade.")

    # --- 2. MÓDULO: ESTOQUISTA CHEFE / ESTOQUISTA ---
    elif perfil_atual in ["Estoquista", "Estoquista Chefe"]:
        st.markdown(f"### ⚙️ Painel do Centro de Estoque — {loja_atual}")
        
        if perfil_atual == "Estoquista Chefe":
            tab_conf, tab_est, tab_xml, tab_manual, tab_baixa = st.tabs([
                "📥 Conferência de Pedidos", "📋 Estoque Atual", "📥 Importar XML", "✍️ Entrada Manual", "⚠️ Baixa com Motivo"
            ])
        else:
            tab_conf, tab_est, tab_xml, tab_manual = st.tabs([
                "📥 Conferência de Pedidos", "📋 Estoque Atual", "📥 Importar XML", "✍️ Entrada Manual"
            ])

        with tab_conf:
            st.markdown("### 📋 Pedidos Pendentes das Lojas")
            conn = sqlite3.connect('sistema_estoque.db')
            df_reqs_estoque = pd.read_sql(f"""
                SELECT r.id_pedido, r.lote_id, r.data, r.solicitante, p.codigo, p.descricao, r.qtd_pedida, r.qtd_estoque_conferencia, r.obs_estoquista, r.status
                FROM requisicoes_loja r
                JOIN produtos p ON r.codigo_produto = p.codigo
                WHERE r.loja = '{loja_atual}' AND r.status = 'Pendente'
            """, conn)
            conn.close()

            if not df_reqs_estoque.empty:
                st.dataframe(df_reqs_estoque, use_container_width=True, hide_index=True)
                
                with st.form("form_conferencia_estoquista"):
                    id_conf = st.selectbox("Selecione o ID do Pedido", df_reqs_estoque['id_pedido'].tolist())
                    item_req = df_reqs_estoque[df_reqs_estoque['id_pedido'] == id_conf].iloc[0]
                    
                    st.info(f"Item: **{item_req['descricao']}** | Qtd Pedida: **{item_req['qtd_pedida']}**")
                    
                    col_e1, col_e2 = st.columns(2)
                    with col_e1:
                        qtd_disp_estoque = st.number_input("Qtd Disponível em Estoque", min_value=0.0, step=1.0)
                    with col_e2:
                        obs_estoq = st.text_input("Anotação / Observação (Opcional)")
                    
                    btn_salvar_conf = st.form_submit_button("💾 Salvar Verificação", use_container_width=True)
                    if btn_salvar_conf:
                        data_hora = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                        conn = sqlite3.connect('sistema_estoque.db')
                        cursor = conn.cursor()
                        cursor.execute("UPDATE requisicoes_loja SET qtd_estoque_conferencia = ?, obs_estoquista = ? WHERE id_pedido = ?",
                                       (qtd_disp_estoque, obs_estoq.strip(), id_conf))
                        
                        cursor.execute("INSERT INTO logs_sistema (data, usuario, loja, tipo_acao, detalhes) VALUES (?, ?, ?, ?, ?)",
                                       (data_hora, st.session_state.usuario, loja_atual, "CONFERENCIA_ESTOQUE", f"Estoquista verificou item #{id_conf}: Estoque disp={qtd_disp_estoque}, Obs={obs_estoq}"))
                        conn.commit()
                        conn.close()
                        st.success("Verificação salva com sucesso!")
                        st.rerun()
            else:
                st.info("Nenhum pedido pendente para conferência.")

        with tab_est:
            st.markdown("### 📦 Saldo Atual e Alertas Mínimos")
            conn = sqlite3.connect('sistema_estoque.db')
            df_estoque = pd.read_sql(f"""
                SELECT p.codigo, p.descricao, p.categoria, p.unidade, COALESCE(SUM(e.quantidade), 0) as quantidade_total, p.estoque_minimo, p.custo
                FROM produtos p
                LEFT JOIN estoque_lotes e ON p.codigo = e.codigo AND e.loja = '{loja_atual}'
                GROUP BY p.codigo
            """, conn)
            conn.close()
            
            if not df_estoque.empty:
                df_estoque['estoque_formatado'] = df_estoque['quantidade_total'].apply(formatar_unidades)
                st.dataframe(df_estoque[['codigo', 'descricao', 'categoria', 'estoque_formatado', 'estoque_minimo', 'custo']], use_container_width=True, hide_index=True)
            else:
                st.info("Nenhum produto cadastrado.")
            
        with tab_xml:
            st.markdown("### 📥 Importação de NF-e (XML)")
            col_x1, col_x2 = st.columns(2)
            with col_x1:
                xml_file = st.file_uploader("Arquivo XML da Nota Fiscal", type=["xml"])
            with col_x2:
                val_xml = st.date_input("Validade Padrão dos Itens")
            
            if xml_file is not None:
                try:
                    tree = ET.parse(xml_file)
                    root = tree.getroot()
                    ns = {'nfe': 'http://www.portalfiscal.inf.br/nfe'}
                    det_list = root.findall('.//nfe:det', ns)
                    
                    itens_nf = []
                    for det in det_list:
                        prod = det.find('nfe:prod', ns)
                        c_prod = prod.find('nfe:cProd', ns).text
                        x_prod = prod.find('nfe:xProd', ns).text
                        q_com = float(prod.find('nfe:qCom', ns).text)
                        v_un = float(prod.find('nfe:vUnCom', ns).text)
                        itens_nf.append({"Código": c_prod, "Descrição": x_prod, "Qtd": q_com, "Custo": v_un})
                    
                    df_nf = pd.DataFrame(itens_nf)
                    st.dataframe(df_nf, use_container_width=True, hide_index=True)
                    
                    if st.button("Confirmar Entrada via XML", use_container_width=True):
                        data_hora = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                        v_str = val_xml.strftime("%Y-%m-%d")
                        conn = sqlite3.connect('sistema_estoque.db')
                        cursor = conn.cursor()
                        for item in itens_nf:
                            cursor.execute("INSERT OR IGNORE INTO produtos (codigo, descricao, categoria, unidade, custo, estoque_minimo) VALUES (?, ?, 'Geral', 'un', ?, 5.0)",
                                           (item["Código"], item["Descrição"], item["Custo"]))
                            cursor.execute("INSERT INTO estoque_lotes (codigo, loja, quantidade, validade) VALUES (?, ?, ?, ?)",
                                           (item["Código"], loja_atual, item["Qtd"], v_str))
                        
                        cursor.execute("INSERT INTO logs_sistema (data, usuario, loja, tipo_acao, detalhes) VALUES (?, ?, ?, ?, ?)",
                                       (data_hora, st.session_state.usuario, loja_atual, "ENTRADA_XML", f"Entrada NF-e. Validade: {v_str}"))
                        conn.commit()
                        conn.close()
                        st.success("Estoque atualizado com sucesso via XML!")
                except Exception as e:
                    st.error(f"Erro ao processar o XML: {e}")

        with tab_manual:
            st.markdown("### ✍️ Entrada Manual de Estoque")
            conn = sqlite3.connect('sistema_estoque.db')
            df_prods_m = pd.read_sql("SELECT codigo, descricao FROM produtos", conn)
            conn.close()
            
            if not df_prods_m.empty:
                with st.form("form_entrada_manual"):
                    prod_sel_m = st.selectbox("Selecione o Produto", df_prods_m['descricao'].tolist())
                    
                    col_m1, col_m2, col_m3 = st.columns(3)
                    with col_m1:
                        qtd_m = st.number_input("Quantidade", min_value=0.1, step=1.0)
                    with col_m2:
                        custo_m = st.number_input("Preço Unitário (R$)", min_value=0.0, step=0.01)
                    with col_m3:
                        val_m = st.date_input("Validade do Lote")
                    
                    btn_ent_man = st.form_submit_button("Confirmar Entrada Manual", use_container_width=True)
                    if btn_ent_man:
                        cod_m = df_prods_m.loc[df_prods_m['descricao'] == prod_sel_m, 'codigo'].values[0]
                        data_hora = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                        v_str = val_m.strftime("%Y-%m-%d")
                        
                        conn = sqlite3.connect('sistema_estoque.db')
                        cursor = conn.cursor()
                        cursor.execute("INSERT INTO estoque_lotes (codigo, loja, quantidade, validade) VALUES (?, ?, ?, ?)",
                                       (cod_m, loja_atual, qtd_m, v_str))
                        cursor.execute("UPDATE produtos SET custo = ? WHERE codigo = ?", (custo_m, cod_m))
                        cursor.execute("INSERT INTO logs_sistema (data, usuario, loja, tipo_acao, detalhes) VALUES (?, ?, ?, ?, ?)",
                                       (data_hora, st.session_state.usuario, loja_atual, "ENTRADA_MANUAL", f"Entrada manual (Val: {v_str})"))
                        conn.commit()
                        conn.close()
                        st.success("Entrada manual registrada com sucesso!")

        if perfil_atual == "Estoquista Chefe":
            with tab_baixa:
                st.markdown("### ⚠️ Baixa Manual de Estoque")
                conn = sqlite3.connect('sistema_estoque.db')
                df_est_b = pd.read_sql(f"""
                    SELECT p.codigo, p.descricao, SUM(e.quantidade) as quantidade
                    FROM produtos p
                    JOIN estoque_lotes e ON p.codigo = e.codigo AND e.loja = '{loja_atual}'
                    GROUP BY p.codigo
                    HAVING quantidade > 0
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
                                conn = sqlite3.connect('sistema_estoque.db')
                                cursor = conn.cursor()
                                cursor.execute("INSERT INTO estoque_lotes (codigo, loja, quantidade, validade) VALUES (?, ?, ?, ?)",
                                               (cod_b, loja_atual, -qtd_baixa, datetime.now().strftime("%Y-%m-%d")))
                                cursor.execute("INSERT INTO logs_sistema (data, usuario, loja, tipo_acao, detalhes) VALUES (?, ?, ?, ?, ?)",
                                               (data_hora, st.session_state.usuario, loja_atual, "BAIXA_ESTOQUE", f"Baixa de {qtd_baixa}. Motivo: {motivo_baixa}"))
                                conn.commit()
                                conn.close()
                                st.success("Baixa realizada com sucesso!")
                                st.rerun()
                else:
                    st.info("Nenhum item com saldo positivo.")

    # --- 3. MÓDULO: ADMINISTRADOR GLOBAL ---
    elif perfil_atual == "Administrador":
        st.markdown("### 👑 Painel Executivo Global")
        
        tab_alertas, tab_lojas, tab_users, tab_prod, tab_editar_prod, tab_excel, tab_visao, tab_logs = st.tabs([
            "🚨 Alertas Globais",
            "🏢 Lojas", 
            "👥 Usuários", 
            "✏️ Cadastro Mestre", 
            "🛠️ Editar Produtos",
            "📊 Excel",
            "🌐 Visão Geral",
            "📜 Logs"
        ])

        with tab_alertas:
            st.markdown("### 🚨 Painel de Executivos: Estoques Críticos e Vencimentos")
            conn = sqlite3.connect('sistema_estoque.db')
            df_abaixo = pd.read_sql("""
                SELECT e.loja, p.codigo, p.descricao, COALESCE(SUM(e.quantidade), 0) as qtd_atual, p.estoque_minimo
                FROM estoque_lotes e
                JOIN produtos p ON e.codigo = p.codigo
                GROUP BY e.loja, p.codigo
                HAVING qtd_atual < p.estoque_minimo
            """, conn)

            limite_str = (datetime.now() + timedelta(days=7)).strftime("%Y-%m-%d")
            df_venc = pd.read_sql(f"""
                SELECT e.loja, e.codigo, p.descricao, e.quantidade, e.validade
                FROM estoque_lotes e
                JOIN produtos p ON e.codigo = p.codigo
                WHERE e.quantidade > 0 AND e.validade <= '{limite_str}'
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
                    st.dataframe(df_venc, use_container_width=True, hide_index=True)
                else:
                    st.success("Nenhum vencimento próximo.")
        
        with tab_lojas:
            st.markdown("### 🏢 Gerenciamento de Lojas")
            with st.form("form_nova_loja"):
                nova_loja = st.text_input("Nome da Nova Loja")
                cad_loja = st.form_submit_button("Cadastrar Loja", use_container_width=True)
                if cad_loja and nova_loja.strip():
                    conn = sqlite3.connect('sistema_estoque.db')
                    cursor = conn.cursor()
                    try:
                        cursor.execute("INSERT INTO lojas VALUES (?)", (nova_loja.strip(),))
                        conn.commit()
                        st.success(f"Loja '{nova_loja}' cadastrada com sucesso!")
                        st.rerun()
                    except:
                        st.error("Esta loja já existe.")
                    conn.close()
                    
            st.markdown("---")
            conn = sqlite3.connect('sistema_estoque.db')
            df_lojas = pd.read_sql("SELECT * FROM lojas", conn)
            conn.close()
            st.dataframe(df_lojas, use_container_width=True, hide_index=True)
                
        with tab_users:
            st.markdown("### 👥 Gerenciamento de Usuários")
            conn = sqlite3.connect('sistema_estoque.db')
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
                    conn = sqlite3.connect('sistema_estoque.db')
                    cursor = conn.cursor()
                    cursor.execute("INSERT OR REPLACE INTO usuarios VALUES (?, ?, ?, ?)", (u_nome.strip(), u_senha, u_perfil, u_loja))
                    conn.commit()
                    conn.close()
                    st.success("Usuário salvo com sucesso!")
                    st.rerun()
                    
            st.markdown("---")
            conn = sqlite3.connect('sistema_estoque.db')
            df_users = pd.read_sql("SELECT * FROM usuarios", conn)
            conn.close()
            st.dataframe(df_users, use_container_width=True, hide_index=True)

        with tab_prod:
            st.markdown("### ✏️ Cadastro Mestre de Produtos")
            with st.form("form_cad_produto"):
                col_p1, col_p2, col_p3 = st.columns(3)
                with col_p1:
                    c_cod = st.text_input("Código")
                    c_desc = st.text_input("Descrição")
                with col_p2:
                    c_cat = st.text_input("Categoria")
                    c_un = st.selectbox("Unidade", ["un", "kg", "pct", "cx", "L"])
                with col_p3:
                    c_min = st.number_input("Estoque Mínimo", min_value=0.0, value=5.0, step=1.0)
                    c_custo = st.number_input("Custo (R$)", min_value=0.0)
                
                salvar_prod = st.form_submit_button("Cadastrar Produto Mestre", use_container_width=True)
                if salvar_prod and c_cod.strip():
                    conn = sqlite3.connect('sistema_estoque.db')
                    cursor = conn.cursor()
                    cursor.execute("INSERT OR REPLACE INTO produtos VALUES (?, ?, ?, ?, ?, ?)",
                                   (c_cod.strip(), c_desc.strip(), c_cat.strip(), c_un, c_custo, c_min))
                    conn.commit()
                    conn.close()
                    st.success("Produto cadastrado com sucesso!")

        with tab_editar_prod:
            st.markdown("### 🛠️ Consulta e Edição de Produtos")
            conn = sqlite3.connect('sistema_estoque.db')
            df_mestre = pd.read_sql("SELECT * FROM produtos", conn)
            conn.close()
            
            if not df_mestre.empty:
                st.dataframe(df_mestre, use_container_width=True, hide_index=True)
                prod_para_editar = st.selectbox("Selecione para alterar", df_mestre['descricao'].tolist())
                item_atual = df_mestre[df_mestre['descricao'] == prod_para_editar].iloc[0]
                
                with st.form("form_edicao_produto"):
                    e_cod = st.text_input("Código", value=str(item_atual['codigo']))
                    e_desc = st.text_input("Descrição", value=str(item_atual['descricao']))
                    e_cat = st.text_input("Categoria", value=str(item_atual['categoria']))
                    unidades_disponiveis = ["un", "kg", "pct", "cx", "L"]
                    idx_un = unidades_disponiveis.index(item_atual['unidade']) if item_atual['unidade'] in unidades_disponiveis else 0
                    e_un = st.selectbox("Unidade", unidades_disponiveis, index=idx_un)
                    e_min = st.number_input("Estoque Mínimo", min_value=0.0, value=float(item_atual['estoque_minimo']), step=1.0)
                    e_custo = st.number_input("Custo (R$)", min_value=0.0, value=float(item_atual['custo']), step=0.01)
                    
                    btn_salvar_edicao = st.form_submit_button("Salvar Alterações", use_container_width=True)
                    if btn_salvar_edicao:
                        conn = sqlite3.connect('sistema_estoque.db')
                        cursor = conn.cursor()
                        cursor.execute("""UPDATE produtos SET codigo = ?, descricao = ?, categoria = ?, unidade = ?, custo = ?, estoque_minimo = ? WHERE codigo = ?""",
                                       (e_cod.strip(), e_desc.strip(), e_cat.strip(), e_un, e_custo, e_min, item_atual['codigo']))
                        conn.commit()
                        conn.close()
                        st.success("Atualizado com sucesso!")
                        st.rerun()
            else:
                st.info("Nenhum produto cadastrado.")
                        
        with tab_excel:
            st.markdown("### 📊 Importação / Exportação Excel")
            conn = sqlite3.connect('sistema_estoque.db')
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
                    conn = sqlite3.connect('sistema_estoque.db')
                    cursor = conn.cursor()
                    for _, row in df_imp.iterrows():
                        cursor.execute("INSERT OR REPLACE INTO produtos VALUES (?, ?, ?, ?, ?, ?)",
                                       (str(row['codigo']), str(row['descricao']), str(row['categoria']), str(row['unidade']), float(row['custo']), float(row.get('estoque_minimo', 5.0))))
                    conn.commit()
                    conn.close()
                    st.success("Importado com sucesso!")

        with tab_visao:
            st.markdown("### 🌐 Visão Global de Estoques")
            conn = sqlite3.connect('sistema_estoque.db')
            df_l = pd.read_sql("SELECT * FROM lojas", conn)
            conn.close()
            lista_lojas_filtro = df_l['nome_loja'].tolist() if not df_l.empty else []
            lista_lojas_filtro.insert(0, "Todas as Lojas (Consolidado)")
            loja_escolhida_admin = st.selectbox("Filtrar por Unidade", lista_lojas_filtro)
            
            conn = sqlite3.connect('sistema_estoque.db')
            if loja_escolhida_admin == "Todas as Lojas (Consolidado)":
                df_geral = pd.read_sql("""
                    SELECT e.loja, p.codigo, p.descricao, p.categoria, SUM(e.quantidade) as quantidade, p.estoque_minimo, p.custo, e.validade
                    FROM estoque_lotes e
                    JOIN produtos p ON e.codigo = p.codigo
                    GROUP BY e.loja, p.codigo, e.validade
                """, conn)
            else:
                df_geral = pd.read_sql(f"""
                    SELECT e.loja, p.codigo, p.descricao, p.categoria, SUM(e.quantidade) as quantidade, p.estoque_minimo, p.custo, e.validade
                    FROM estoque_lotes e
                    JOIN produtos p ON e.codigo = p.codigo
                    WHERE e.loja = '{loja_escolhida_admin}'
                    GROUP BY p.codigo, e.validade
                """, conn)
            conn.close()
            
            if not df_geral.empty:
                df_geral['estoque_formatado'] = df_geral['quantidade'].apply(formatar_unidades)
                st.dataframe(df_geral, use_container_width=True, hide_index=True)
            else:
                st.info("Nenhum estoque registrado.")

        with tab_logs:
            st.markdown("### 📜 Log Completo de Auditoria")
            conn = sqlite3.connect('sistema_estoque.db')
            df_logs = pd.read_sql("SELECT * FROM logs_sistema ORDER BY id_log DESC", conn)
            conn.close()
            
            if not df_logs.empty:
                st.dataframe(df_logs, use_container_width=True, hide_index=True)
                log_id = st.selectbox("Inspecionar ID do Log", df_logs['id_log'].tolist())
                if log_id:
                    row_l = df_logs[df_logs['id_log'] == log_id].iloc[0]
                    st.info(f"**Data:** {row_l['data']} | **Usuário:** {row_l['usuario']} | **Ação:** {row__l['tipo_acao'] if 'tipo_acao' in row_l else row_l[4]}")
                    st.success(row_l['detalhes'])
            else:
                st.info("Nenhum log registrado.")
