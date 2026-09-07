import json
from pathlib import Path
import sys
from unittest import mock

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from scripts import research, material_review
from jinja2 import Environment, FileSystemLoader

SOURCE=[{'description':'統計の説明','url':'https://example.org/table','section':'貿易'}]


def test_collection_stays_claude_and_content_decisions_use_astra():
    with mock.patch.object(research._subscription,'generate',return_value=('ok',{})) as call:
        research.claude_research(None,'q','s')
        assert call.call_args.kwargs['model']==research.RESEARCH_MODEL
        assert call.call_args.kwargs['use_search'] is True
        research.claude_query(None,'q','s')
        assert call.call_args.kwargs['model']=='gpt-6-astra'
        assert call.call_args.kwargs['effort']=='high'


def test_review_uses_approved_full_input_and_only_codex_subscription():
    answer={'items':[{'id':'Web1','status':'pass','source_checked':True,'reason':'表の定義・期間が一致'}],'gaps':[]}
    with mock.patch.object(material_review.runtime,'generate',return_value=(json.dumps(answer),{})) as call:
        result=material_review.review_materials('原稿前半\n原稿末尾',SOURCE)
    assert result['status']=='pass'
    assert '原稿末尾' in call.call_args.args[1]
    assert call.call_args.kwargs['primary']=='codex'
    assert call.call_args.kwargs['allow_fallback'] is False
    assert call.call_args.kwargs['use_search'] is True


def test_unsupported_pass_cannot_become_verified_or_change_source_url():
    answer={'items':[{'id':'Web1','status':'pass','source_checked':False,'reason':'断片のみ','url':'https://wrong.example'}]}
    with mock.patch.object(material_review.runtime,'generate',return_value=(json.dumps(answer),{})):
        result=material_review.review_materials('原稿',SOURCE)
    assert result['status']=='unverified'
    assert result['items'][0]['url']==SOURCE[0]['url']


def test_unavailable_cli_stops_after_one_call():
    with mock.patch.object(material_review.runtime,'generate',side_effect=material_review.runtime.SubscriptionUnavailable('usage_limit')) as call:
        result=material_review.review_materials('原稿',SOURCE)
    assert result['status']=='unverified'
    assert call.call_count==1


def test_missing_or_duplicate_entries_remain_unverified():
    entry={'id':'Web1','status':'pass','source_checked':True,'reason':'一致'}
    with mock.patch.object(material_review.runtime,'generate',return_value=(json.dumps({'items':[entry,entry]}),{})):
        result=material_review.review_materials('原稿',SOURCE)
    assert result['status']=='unverified'


def test_output_template_displays_review_and_escapes_model_text():
    env=Environment(loader=FileSystemLoader(str(Path(__file__).resolve().parents[1]/'template')),autoescape=False)
    review={'status':'needs_fix','summary':'<script>alert(1)</script>','items':[{'id':'Web1','status':'unverified','description':'統計','reason':'未確認'}],'gaps':['定義の資料が不足']}
    rendered=env.get_template('index.html').render(content_review=review,sections=[],youtube_videos=[],web_data=[],diagram_images=[],realistic_images=[])
    assert '資料の内容検査：要修正' in rendered
    assert 'Web1 — 未確認' in rendered
    assert '定義の資料が不足' in rendered
    assert '<script>alert(1)</script>' not in rendered
    assert '&lt;script&gt;' in rendered
