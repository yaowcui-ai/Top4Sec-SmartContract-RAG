"""Deterministic PDF-page chunks and bibliographic KG; no generated semantic claims."""
import re

def page_chunks(paper,pages,tokenizer,size=1200,overlap=100):
    chunks=[]
    for page in pages:
        if not page['text'].strip():continue
        header=(f'Title: {paper["title"]}\nPaper-ID: {paper["paper_id"]}\n'
            f'Conference (catalog label): {paper["venue"]}\nYear: {paper["year"]}\nPDF page: {page["page"]}\n\n')
        maximum=size-len(tokenizer.encode(header))
        if maximum<=overlap:raise ValueError('Title/header too long for chunk budget')
        tokens=tokenizer.encode(page['text']);step=maximum-overlap
        for order,start in enumerate(range(0,len(tokens),step)):
            body=tokenizer.decode(tokens[start:start+maximum])
            chunks.append({'content':header+body,'source_id':f'{paper["paper_id"]}:p{page["page"]}:c{order}',
                'file_path':f'{paper["paper_id"]}__p{page["page"]:04d}_{order}.md','chunk_order_index':len(chunks)})
            if start+maximum>=len(tokens):break
    return chunks

def build_custom_kg(paper,chunks):
    if not chunks:raise ValueError('Cannot index an empty paper')
    first=chunks[0];name='Paper: '+paper['title']
    venue=f'Conference: {paper["venue"]}';year=f'Publication year: {paper["year"]}'
    def entity(identifier,kind,description,chunk):
        return {'entity_name':identifier,'entity_type':kind,'description':description,
            'source_id':chunk['source_id'],'file_path':chunk['file_path']}
    entities=[entity(name,'PAPER',f'{paper["title"]}. Catalog conference {paper["venue"]}, year {paper["year"]}.\n'+first['content'][:1500],first),
        entity(venue,'CONFERENCE',f'The source catalog lists this paper under {paper["venue"]}; this is a catalog label, not independent bibliographic verification.',first),
        entity(year,'YEAR',f'Publication year label {paper["year"]} in the source catalog.',first)]
    def relation(src,tgt,description,keywords,chunk):
        return {'src_id':src,'tgt_id':tgt,'description':description,'keywords':keywords,'weight':1.0,
            'source_id':chunk['source_id'],'file_path':chunk['file_path']}
    relationships=[relation(name,venue,f'{paper["title"]} is cataloged under {paper["venue"]}.','catalog,conference,publication',first),
        relation(name,year,f'The catalog labels {paper["title"]} with year {paper["year"]}.','year,publication',first)]
    for chunk in chunks:
        section=f'{paper["title"][:110]} | {chunk["source_id"]}'
        entities.append(entity(section,'PAPER_PAGE_CHUNK',chunk['content'][:2200],chunk))
        relationships.append(relation(name,section,'This full-text excerpt belongs to the named paper and carries its PDF page number.','paper,full text,page,evidence',chunk))
    return {'chunks':chunks,'entities':entities,'relationships':relationships}
