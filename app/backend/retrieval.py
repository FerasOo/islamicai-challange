import json
import sys
import time
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from .config import PROJECT, SOURCE_NAMES, ACTIVE_SOURCE_NAMES, CANDIDATE_LIMIT, RETRIEVAL_MINIMUM

sys.path.insert(0,str(PROJECT/'retrieval/scripts'))


def reference(metadata):
    if metadata.get('surah'):
        return f"السورة {metadata['surah']} · الآية {metadata.get('ayah','')}"
    if metadata.get('hadeethenc_id'):
        return 'شرح الحديث '+str(metadata['hadeethenc_id'])
    if metadata.get('edition_reference'):
        return 'رقم الرواية '+str(metadata['edition_reference'])
    if metadata.get('pdf_pages'):
        return 'صفحة PDF '+ '، '.join(map(str,metadata['pdf_pages'][:5]))
    pages = [p.get('printed','') for p in metadata.get('pages',[]) if p.get('printed')]
    return ' / '.join(dict.fromkeys(pages[:3])) or 'موضع محفوظ في المصدر'


class Retrieval:
    def __init__(self):
        self.executor = ThreadPoolExecutor(max_workers=1,thread_name_prefix='jina-retrieval')
        self.engine = None
        self.info = {}
        self.query_cache = OrderedDict()

    def load(self):
        from search import EvidenceSearch
        self.engine = EvidenceSearch()
        self.info = {'partial':False,'indexed':self.engine.table.count_rows()}
        active_filter = 'source IN ('+','.join("'"+s+"'" for s in ACTIVE_SOURCE_NAMES)+')'
        self.info.update({'stored_indexed':self.info['indexed'],
            'indexed':self.engine.table.count_rows(active_filter),
            'total':sum(r[1] for r in self.engine.db.execute('SELECT source,COUNT(*) FROM chunks GROUP BY source') if r[0] in ACTIVE_SOURCE_NAMES)})
        self.engine.model.encode(['اختبار تهيئة البحث'],query=True)
        return self.info

    def search(self, query, k=CANDIDATE_LIMIT, sources=None, minimum=RETRIEVAL_MINIMUM):
        from search import normalize
        if sources and any(s not in ACTIVE_SOURCE_NAMES for s in sources):
            raise ValueError('المصدر غير متاح للبحث.')
        active_sources = sources or list(ACTIVE_SOURCE_NAMES)
        start = time.perf_counter()
        key = normalize(query)
        cached = key in self.query_cache
        if cached:
            v = self.query_cache[key];self.query_cache.move_to_end(key)
        else:
            v = self.engine.model.encode([key],query=True)[0]
            self.query_cache[key] = v
            if len(self.query_cache)>256:self.query_cache.popitem(last=False)
        embedded = time.perf_counter()
        limit = min(max(int(k),1),CANDIDATE_LIMIT)
        hits = self.engine.rank(v,limit,sources=active_sources)[:limit]
        # Filter raw cosine scores before loading source text or calling Jev.
        # Display rounding must never admit a passage below the actual cutoff.
        selected = [h for h in hits if 1-h['_distance'] >= minimum]
        ranked = time.perf_counter()
        docs = self.engine.hydrate(selected)
        docs = [self.decorate(d) for d in docs]
        return docs,{'embedding_cached':cached,'embedding_ms':1000*(embedded-start),'search_ms':1000*(ranked-embedded),
                    'fetch_ms':1000*(time.perf_counter()-ranked),'retrieval_ms':1000*(time.perf_counter()-start),
                    'minimum':minimum,'candidate_limit':limit,'ranked':len(hits),'candidates':len(docs),
                    'cap_reached':len(selected)==limit}

    def decorate(self, doc):
        d = dict(doc)
        row = self.engine.db.execute('SELECT metadata FROM parents WHERE id=?',(d['parent_id'],)).fetchone()
        meta = json.loads(row['metadata'])
        if d['source']=='muslim' and meta.get('edition_reference'):
            import re
            canonical = re.match(r'^\s*\d+\s*[-–]\s*\(([^)]+)\)',d['text'])
            if canonical:meta['edition_reference'] = str(meta['edition_reference']).strip(' -')+' · رقم الأصل '+canonical.group(1)
        section_start = meta.get('source_section_char_range',[0])[0]
        if meta.get('pages'):
            start,end = section_start+d.get('start_char',0),section_start+d.get('end_char',len(d['text']))
            meta['pages'] = [p for p in meta['pages'] if p.get('section_start_char',0) < end and p.get('section_end_char',float('inf')) > start]
        d['title'] = d['title'].replace('سورة سورة','سورة')
        d.update({'source_name':SOURCE_NAMES[d['source']],'reference':reference(meta),
            'similarity':round(1-d.pop('distance',1),4),'review_required':bool(meta.get('requires_source_review')),
            'source_url':meta.get('url'),'status':'pending'})
        return d

    def chunk(self, chunk_id):
        row = self.engine.db.execute('SELECT id,parent_id,source,title,text,start_char,end_char FROM chunks WHERE id=?',(chunk_id,)).fetchone()
        return self.decorate(dict(row)) if row else None

    def context(self, pid):
        result = self.engine.context([pid])
        for parent in result['parents'].values():
            parent['metadata'].pop('original',None)
        return result

    def passage(self, doc):
        # Context-dependent verse/variant and boundary companions are part of the
        # judgment input, not an assumed interpretation generated by the app.
        context = self.engine.context([doc['parent_id']])
        extra = []; incomplete = False; seen = set()
        for link in context['links']:
            if link['relation'].startswith('required_'):
                parent = context['parents'].get(link['to'])
                if link['to'] in seen:continue
                seen.add(link['to'])
                if parent and len(parent['text']) <= 16000:
                    extra.append({'title':parent['title'],'text':parent['text']})
                else:incomplete = True
        return {'title':doc['title'],'quotation':doc['text'],'source':doc['source_name'],
                'reference':doc['reference'],'required_source_context':extra,
                'source_review_required':doc['review_required'],'required_context_incomplete':incomplete}

    def close(self):
        self.executor.shutdown(wait=False,cancel_futures=True)
