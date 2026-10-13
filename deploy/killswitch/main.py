"""Disjuntor de gastos: desliga o faturamento do projeto quando o gasto passa do orçamento.

Recebe as notificações do orçamento via Pub/Sub. Desligar o faturamento derruba o site;
para religar, vincule a conta de faturamento de novo e rode deploy/cloudrun.sh.
"""
import base64
import json
import os

import functions_framework
from googleapiclient import discovery

PROJECT_ID = os.environ['PROJECT_ID']


@functions_framework.cloud_event
def stop_billing(cloud_event):
    data = json.loads(base64.b64decode(cloud_event.data['message']['data']).decode('utf-8'))
    cost, budget = data.get('costAmount', 0), data.get('budgetAmount', 0)
    if cost <= budget:
        print(f'gasto {cost} dentro do orçamento {budget}; nada a fazer')
        return
    billing = discovery.build('cloudbilling', 'v1', cache_discovery=False)
    name = f'projects/{PROJECT_ID}'
    if billing.projects().getBillingInfo(name=name).execute().get('billingEnabled'):
        billing.projects().updateBillingInfo(name=name, body={'billingAccountName': ''}).execute()
        print(f'gasto {cost} passou do orçamento {budget}: faturamento DESLIGADO')
