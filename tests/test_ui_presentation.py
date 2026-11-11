"""Participant copy/layout boundaries, without changing inference or research data."""
import tempfile
import unittest
from html.parser import HTMLParser
from app import create_app
from core.config import Settings

class Elements(HTMLParser):
    def __init__(self):
        super().__init__();self.stack=[];self.parents={};self.attrs={}
    def handle_starttag(self,tag,attrs):
        attrs=dict(attrs)
        if attrs.get('id'):
            self.parents[attrs['id']]=list(self.stack)
            self.attrs[attrs['id']]=attrs
        if tag not in {'br','meta','link','input','img','path','circle'}:self.stack.append((tag,attrs.get('id'),attrs))
    def handle_endtag(self,tag):
        for i in range(len(self.stack)-1,-1,-1):
            if self.stack[i][0]==tag:
                self.stack=self.stack[:i];break

class UIPresentationTests(unittest.TestCase):
    def page(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
        app=create_app(Settings(data_dir=temp.name,mode='preview',api_key=''))
        html=app.test_client().get('/').get_data(as_text=True)
        parser=Elements();parser.feed(html)
        return html,parser

    def test_primary_state_flow_precedes_collapsed_detail_and_keeps_evidence(self):
        html,p=self.page()
        self.assertLess(html.index('id="changeSection"'),html.index('id="nextSection"'))
        self.assertLess(html.index('id="nextSection"'),html.index('id="stateDetails"'))
        self.assertNotIn('open',p.attrs['stateDetails'])
        self.assertTrue(any(x[1]=='stateDetails' for x in p.parents['clarifiedAspects']))
        self.assertTrue(any(x[1]=='stateDetails' for x in p.parents['researchProblem']))
        self.assertIn('evidenceSection',p.attrs)
        self.assertIn('historyList',p.attrs)
        self.assertFalse(any(x[0]=='details' for x in p.parents['nextStep']))

    def test_copy_scripts_load_before_app_and_operator_instructions_are_collapsed(self):
        html,p=self.page()
        self.assertLess(html.index('/static/ui-copy.js'),html.index('/static/app.js'))
        self.assertNotIn('WORKSPACE',html)
        self.assertNotIn('RESEARCH NOTE',html)
        self.assertNotIn('class="pane-index"',html)
        self.assertIn('연구자용 연결 정보',html)
        self.assertIn('LLM을 활용한 연구 주제 탐색',html)
        self.assertIn('AI 추천과 사용자의 선택 주도성',html)
        self.assertIn('낯선 분야를 탐색하는 연구자의 어려움',html)
