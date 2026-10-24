from app.index_plan import page_chunks,build_custom_kg

class Tokenizer:
    def encode(self,text):return text.split()
    def decode(self,words):return ' '.join(words)

PAPER={'paper_id':'sp_2024_abc','title':'Contract Analysis','venue':'SP','year':2024}

def test_chunking_never_loses_pdf_page_and_has_overlap():
    chunks=page_chunks(PAPER,[{'page':7,'text':' '.join(f'w{i}' for i in range(1800))}],Tokenizer(),1200,100)
    assert len(chunks)==2
    assert all(c['file_path'].startswith('sp_2024_abc__p0007') for c in chunks)
    assert 'w1100' in chunks[0]['content'] and 'w1100' in chunks[1]['content']
    assert 'PDF page: 7' in chunks[0]['content']

def test_custom_graph_is_grounded_in_chunk_aliases_not_invented_semantics():
    chunks=page_chunks(PAPER,[{'page':1,'text':'contract evidence '*50}],Tokenizer())
    graph=build_custom_kg(PAPER,chunks)
    aliases={c['source_id'] for c in graph['chunks']}
    assert all(e['source_id'] in aliases for e in graph['entities'])
    assert all(r['source_id'] in aliases for r in graph['relationships'])
    assert any(e['entity_type']=='PAPER' for e in graph['entities'])
    assert not any('exploits vulnerability' in r['description'] for r in graph['relationships'])
