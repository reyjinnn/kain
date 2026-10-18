import json
import os
import socket
import unittest
from copy import deepcopy
from unittest import mock
from fastapi.testclient import TestClient
from backend import app as service
from backend.design import engine
from backend.services import ai


class CompactPackageTests(unittest.TestCase):
    def setUp(self):
        self.data=engine.fixture_data();self.sig=engine.signature(self.data)
        self.reference=engine.search_layout(self.data,self.sig,variants=['whole'])['candidate']
        self.payload={'action':'describe_checked_plan','assembly_step_ids':[1,3,4,5,6],
                      'notes':{field:'Periksa isian kamu sebelum memakai pola.' for field in ai.PACKAGE_FIELDS}}
        self.payload['notes']['summary']='Pouch {W_cm} × {H_cm} cm, depan dari {front_fabric_name}.'

    def test_code_fills_only_registered_values_and_geometry_is_unchanged(self):
        result=ai.validate_package(self.payload,self.data,self.reference,self.sig)
        self.assertIn('16 × 12 cm',result['ai_package']['summary'])
        self.assertIn('Kain biru',result['ai_package']['summary'])
        self.assertEqual(result['patterns'],self.reference['patterns'])
        self.assertEqual(result['placements'],self.reference['placements'])
        self.assertEqual(result['assembly_steps'],self.reference['assembly_steps'])
        self.assertTrue(engine.validate_candidate(self.data,result,self.sig,activation=True)['passed'])

    def test_invented_numbers_operations_placeholders_order_and_geometry_rejected(self):
        for change in ('literal','operation','attribute','unfilled','order','geometry'):
            value=deepcopy(self.payload)
            if change=='literal':value['notes']['summary']='Pouch 18 × 14 cm.'
            elif change=='operation':value['notes']['watch_out']='Tambahkan ritsleting.'
            elif change=='attribute':value['notes']['summary']='{W_cm.__class__}'
            elif change=='unfilled':value['notes']['summary']='{{W_cm}}'
            elif change=='order':value['assembly_step_ids']=[1,4,3,5,6]
            else:value['placements']=[]
            with self.subTest(change=change),self.assertRaises(ai.ModelResponseError):
                ai.validate_package(value,self.data,self.reference,self.sig)

    def test_model_writes_notes_instead_of_echoing_geometry(self):
        with mock.patch.dict(os.environ,{'NINEROUTER_KEY':'test-key'}),mock.patch.object(ai,'_call_chat_api',return_value=json.dumps(self.payload)) as call:
            result,trace=ai.generate_plan(self.data,self.reference,self.sig)
        schema=call.call_args.args[2]
        self.assertEqual(set(schema['properties']),{'action','assembly_step_ids','notes'})
        self.assertTrue(trace['live_model_used']);self.assertIn('16 × 12 cm',result['ai_package']['summary'])

    def test_package_timeout_preserves_real_reason_and_checked_automatic_plan(self):
        client=TestClient(service.app);client.post('/api/reset',json={})
        with mock.patch.dict(os.environ,{'NINEROUTER_KEY':'test-key'}),mock.patch.object(ai,'_call_chat_api',side_effect=socket.timeout()):
            response=client.post('/api/plan',json={'use_ai':True,'ai_consent':True})
        self.assertEqual(response.status_code,200)
        result=response.json();self.assertEqual(result['ai_trace']['reason'],'AI_TIMEOUT')
        self.assertFalse(result['ai_trace']['live_model_used'])
        self.assertTrue(result['active_revision']['validation_report']['passed'])
