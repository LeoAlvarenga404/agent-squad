"""Synthetic set (fictitious customers and conversations, pt-BR data) to evaluate per-customer memory."""

CUSTOMERS = {
    "padaria-pao-quente": "Padaria de bairro, dona atende no balcão",
    "clinica-sorriso-sul": "Clínica odontológica com dois dentistas",
    "oficina-torque-forte": "Oficina mecânica e auto center",
}

# key: (slug, kind, text)
ITEMS = {
    # ── padaria-pao-quente ──
    "A1": ("padaria-pao-quente", "preference", "A dona gosta de ser tratada pelo primeiro nome, de forma bem informal, com emojis. Não gosta de 'senhora'."),
    "A2": ("padaria-pao-quente", "preference", "Só responde no WhatsApp. Ligação ela não atende porque está sempre com a mão na massa."),
    "A3": ("padaria-pao-quente", "preference", "Melhor horário para falar com ela é depois das 14h, quando o forno já parou. De manhã cedo ela não vê o celular."),
    "A4": ("padaria-pao-quente", "conversation", "Achou R$ 1.500 caro para o site e disse que o primo faz por R$ 600. Pediu para eu explicar o que ela ganha a mais."),
    "A5": ("padaria-pao-quente", "decision", "Combinamos parcelar em 3 vezes no Pix, a primeira na aprovação do preview."),
    "A6": ("padaria-pao-quente", "preference", "Quer o site com cores quentes, marrom e amarelo trigo, e fotos reais dos pães dela, nada de banco de imagens."),
    "A7": ("padaria-pao-quente", "fact", "Abre às 5h30 e fecha às 19h, de segunda a sábado. Domingo só até meio-dia."),
    "A8": ("padaria-pao-quente", "decision", "Prazo combinado: site no ar antes do Dia das Mães, ela mesma pediu pressa por causa das encomendas de bolo."),
    "A9": ("padaria-pao-quente", "conversation", "Perguntou se dá para o cliente encomendar bolo pelo site. Expliquei que por enquanto só um botão que abre o WhatsApp com a mensagem pronta."),
    "A10": ("padaria-pao-quente", "preference", "Mensagens curtas, uma ideia por vez. Se mando texto grande ela não lê e some por dias."),
    # ── clinica-sorriso-sul ──
    "B1": ("clinica-sorriso-sul", "preference", "O responsável é dentista e quer ser chamado de 'doutor', tom formal e técnico. Sem gírias nem emojis."),
    "B2": ("clinica-sorriso-sul", "preference", "Prefere e-mail com tudo por escrito, para encaminhar à sócia. WhatsApp só para avisos rápidos."),
    "B3": ("clinica-sorriso-sul", "preference", "Reuniões por vídeo só à noite, depois das 20h, porque durante o dia atende pacientes."),
    "B4": ("clinica-sorriso-sul", "conversation", "Não reclamou do preço, mas quer ver quantos pacientes novos o site traz antes de contratar a manutenção mensal."),
    "B5": ("clinica-sorriso-sul", "decision", "Pagamento por boleto bancário com nota fiscal, 50% na assinatura e 50% na entrega. Não usa Pix para a empresa."),
    "B6": ("clinica-sorriso-sul", "preference", "Design limpo, azul claro e branco, transmitir confiança. Não quer foto de sorriso de banco de imagens, só fotos da equipe e da clínica."),
    "B7": ("clinica-sorriso-sul", "fact", "A sócia também é dentista e aprova qualquer alteração de texto antes de publicar, especialmente o que cita tratamentos, por causa das regras do conselho."),
    "B8": ("clinica-sorriso-sul", "decision", "Entrega pedida para o fim do mês, quando começa a campanha de clareamento. Sem urgência antes disso."),
    "B9": ("clinica-sorriso-sul", "fact", "Tem agendamento online pelo sistema Dentalweb e quer um botão de agendar que leve direto para ele."),
    "B10": ("clinica-sorriso-sul", "conversation", "Demora de dois a três dias para responder e fica incomodado se eu cobro antes disso. Respeitar esse ritmo."),
    # ── oficina-torque-forte ──
    "C1": ("oficina-torque-forte", "preference", "O dono é direto e meio desconfiado, quer ir ao ponto, sem enrolação nem palavra difícil. Chama todo mundo de 'chefe'."),
    "C2": ("oficina-torque-forte", "preference", "Manda áudio e prefere receber áudio. Odeia ler textão. Atende ligação a qualquer hora, menos na segunda de manhã."),
    "C3": ("oficina-torque-forte", "conversation", "Disse que já foi enganado por outra agência que sumiu depois de receber. Quer pagar só quando ver o site pronto funcionando."),
    "C4": ("oficina-torque-forte", "decision", "Aceitou pagar à vista no cartão de crédito com desconto de 5%, mas só depois de aprovar o preview."),
    "C5": ("oficina-torque-forte", "preference", "Quer destacar as marcas que atende e uma galeria de antes e depois dos serviços. Preto e laranja, visual forte."),
    "C6": ("oficina-torque-forte", "fact", "Atende de segunda a sexta das 8h às 18h e sábado até 13h. Fica numa avenida movimentada, tem estacionamento próprio."),
    "C7": ("oficina-torque-forte", "conversation", "Reclamou que o orçamento de R$ 2.000 está acima do que esperava e pediu um plano mais enxuto, só com o essencial."),
    "C8": ("oficina-torque-forte", "decision", "Prazo: quer ver o primeiro preview em até uma semana, senão cancela. Combinado preview na sexta."),
    "C9": ("oficina-torque-forte", "fact", "Quer aparecer no Google Maps quando buscam 'mecânico perto de mim'. Já tem perfil no Google, mas está desatualizado."),
}

# (slug, question, expected key)
QUESTIONS = [
    ("padaria-pao-quente", "Como devo chamar a dona?", "A1"),
    ("padaria-pao-quente", "Posso ligar para ela?", "A2"),
    ("padaria-pao-quente", "Que horas é bom mandar mensagem?", "A3"),
    ("padaria-pao-quente", "Ela reclamou do valor?", "A4"),
    ("padaria-pao-quente", "Como será o pagamento?", "A5"),
    ("padaria-pao-quente", "Que paleta de cores e imagens ela quer?", "A6"),
    ("padaria-pao-quente", "Tem data limite para entregar?", "A8"),
    ("padaria-pao-quente", "Dá para fazer pedido de bolo online?", "A9"),
    ("padaria-pao-quente", "Textos longos funcionam com ela?", "A10"),
    ("clinica-sorriso-sul", "Qual o tratamento de pronome e o tom com o responsável?", "B1"),
    ("clinica-sorriso-sul", "Por qual canal ele prefere receber as propostas?", "B2"),
    ("clinica-sorriso-sul", "Quando marcar videochamada?", "B3"),
    ("clinica-sorriso-sul", "Ele acha caro ou quer ver resultado antes?", "B4"),
    ("clinica-sorriso-sul", "Aceita Pix? Como emitir a cobrança?", "B5"),
    ("clinica-sorriso-sul", "Quem precisa aprovar os textos sobre procedimentos?", "B7"),
    ("clinica-sorriso-sul", "Qual o prazo desejado para o site ficar pronto?", "B8"),
    ("clinica-sorriso-sul", "Como integrar a marcação de consultas?", "B9"),
    ("clinica-sorriso-sul", "Em quanto tempo ele costuma responder?", "B10"),
    ("oficina-torque-forte", "O dono gosta de ler mensagem comprida?", "C2"),
    ("oficina-torque-forte", "Ele tem medo de calote ou golpe de agência?", "C3"),
    ("oficina-torque-forte", "Existe desconto para pagamento à vista?", "C4"),
    ("oficina-torque-forte", "Que estilo visual e conteúdo ele quer mostrar?", "C5"),
    ("oficina-torque-forte", "O orçamento ficou acima do esperado?", "C7"),
    ("oficina-torque-forte", "Em quanto tempo ele espera ver algo pronto?", "C8"),
    ("oficina-torque-forte", "Como aparecer nas buscas do Google por perto?", "C9"),
]
