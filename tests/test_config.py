from pathlib import Path
import pytest
from dotenv import dotenv_values
from scripts import init_config
from app.settings import Settings,ROOT

def test_first_install_writes_complete_supplier_templates(tmp_path,monkeypatch):
    (tmp_path/'.lightrag.env.example').write_bytes((ROOT/'.lightrag.env.example').read_bytes())
    monkeypatch.setattr(init_config,'ROOT',tmp_path);monkeypatch.setattr('sys.argv',['init_config.py'])
    init_config.main();values=dotenv_values(tmp_path/'.lightrag.env')
    assert values['EMBEDDING_MODEL']=='BAAI/bge-m3'
    assert values['EMBEDDING_DIM']=='1024'
    assert values['LLM_BINDING']=='openai'
    assert not values['EMBEDDING_BINDING_API_KEY']
    assert values['LIGHTRAG_API_KEY']==dotenv_values(tmp_path/'.env')['LIGHTRAG_API_KEY']

def test_init_never_overwrites_existing_private_config(tmp_path,monkeypatch):
    (tmp_path/'.env').write_text('existing-private-content')
    monkeypatch.setattr(init_config,'ROOT',tmp_path);monkeypatch.setattr('sys.argv',['init_config.py'])
    with pytest.raises(SystemExit):init_config.main()
    assert (tmp_path/'.env').read_text()=='existing-private-content'

def test_production_settings_reject_the_old_database(monkeypatch):
    monkeypatch.setenv('MYSQL_DATABASE','doctrace')
    with pytest.raises(ValueError,match='smartcontract_papers'):Settings.load()
