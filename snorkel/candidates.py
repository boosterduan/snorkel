from .models import CandidateSet, TemporarySpan, SpanPair, Phrase
from itertools import chain
from multiprocessing import Process, Queue, JoinableQueue
from Queue import Empty
from utils import get_as_dict
import re
from utils import expand_implicit_text
from parser import CoreNLPHandler

QUEUE_COLLECT_TIMEOUT = 5


def gold_stats(candidates, gold):
        """Return precision and recall relative to a "gold" CandidateSet"""
        # TODO: Make this efficient via SQL
        nc   = len(candidates)
        ng   = len(gold)
        both = len(gold.intersection(candidates.candidates))
        print "# of gold annotations\t= %s" % ng
        print "# of candidates\t\t= %s" % nc
        print "Candidate recall\t= %0.3f" % (both / float(ng),)
        print "Candidate precision\t= %0.3f" % (both / float(nc),)


class CandidateSpace(object):
    """
    Defines the **space** of candidate objects
    Calling _apply(x)_ given an object _x_ returns a generator over candidates in _x_.
    """
    def __init__(self):
        pass

    def apply(self, x):
        raise NotImplementedError()


class CandidateExtractorProcess(Process):
    def __init__(self, candidate_space, matcher, contexts_in, candidates_out):
        Process.__init__(self)
        self.candidate_space = candidate_space
        self.matcher         = matcher
        self.contexts_in     = contexts_in
        self.candidates_out  = candidates_out

    def run(self):
        while True:
            try:
                context = self.contexts_in.get(False)
                for candidate in self.matcher.apply(self.candidate_space.apply(context)):
                    self.candidates_out.put(candidate, False)
                self.contexts_in.task_done()
            except Empty:
                break


class CandidateExtractor(object):
    """
    A generic class to create a Candidates object, which is a set of Candidate objects.

    Takes in a CandidateSpace operator over some context type (e.g. Ngrams, applied over Sentence objects),
    a Matcher over that candidate space, and a set of context objects (e.g. Sentences)
    """
    def __init__(self, parallelism=False, join_key='context_id'):
        self.parallelism = parallelism
        self.join_key = join_key

        self.ps = []
        self.feats = None
        self.feat_index = {}

    def extract(self, contexts, name=None):
        c = CandidateSet()

        if self.parallelism in [1, False]:
            for candidate in self._extract(contexts):
                c.candidates.append(candidate.promote())
        else:
            for candidate in self._extract_multiprocess(contexts):
                c.candidates.append(candidate.promote())

        if name is not None:
            c.name = name

        return c

    def _extract(self, contexts):
        raise NotImplementedError

    def _extract_multiprocess(self, contexts):
        contexts_in    = JoinableQueue()
        candidates_out = Queue()

        # Fill the in-queue with contexts
        for context in contexts:
            contexts_in.put(context)

        # Start worker Processes
        for i in range(self.parallelism):
            p = CandidateExtractorProcess(self.candidate_space, self.matcher, contexts_in, candidates_out)
            p.start()
            self.ps.append(p)

        # Join on JoinableQueue of contexts
        contexts_in.join()

        # Collect candidates out
        candidates = []
        while True:
            try:
                candidates.append(candidates_out.get(True, QUEUE_COLLECT_TIMEOUT))
            except Empty:
                break
        return candidates

    def _index(self, candidates):
        self._candidates_by_id         = {}
        self._candidates_by_context_id = defaultdict(list)
        for c in candidates:
            self._candidates_by_id[c.id] = c
            self._candidates_by_context_id[c.__dict__[self.join_key]].append(c)

    def __iter__(self):
        """Default iterator is over Candidates"""
        return self._candidates_by_id.itervalues()

    def get_candidates(self):
        return self._candidates_by_id.values()

    def get_candidate(self, id):
        """Retrieve a candidate by candidate id"""
        return self._candidates_by_id[id]

    def get_candidates_in(self, context_id):
        """Return the candidates in a specific context (e.g. Sentence)"""
        return self._candidates_by_context_id[context_id]

    def gold_stats(self, gold_set):
        """Return precision and recall relative to a "gold" set of candidates of the same type"""
        gold = gold_set if isinstance(gold_set, set) else set(gold_set)
        cs   = self.get_candidates()
        nc   = len(cs)
        ng   = len(gold)
        both = len(gold.intersection(cs))
        print "# of gold annotations\t= %s" % ng
        print "# of candidates\t\t= %s" % nc
        print "Candidate recall\t= %0.3f" % (both / float(ng),)
        print "Candidate precision\t= %0.3f" % (both / float(nc),)


class EntityExtractor(CandidateExtractor):
    def __init__(self, candidate_space, matcher, parallelism=False, join_key='context_id'):
        super(EntityExtractor, self).__init__(parallelism=parallelism, join_key=join_key)
        self.candidate_space = candidate_space
        self.matcher = matcher

    def _extract(self, contexts):
        return chain.from_iterable(self.matcher.apply(self.candidate_space.apply(c)) for c in contexts)


class RelationExtractor(CandidateExtractor):
    """Temporary class for getting quick numbers"""
    def __init__(self, extractor1, extractor2, join_key='context_id'):
        super(RelationExtractor, self).__init__(parallelism=False, join_key=join_key)
        self.e1 = extractor1
        self.e2 = extractor2

    def _extract(self, contexts):
        for context in contexts:
            for span0 in self.e1._extract([context]):
                for span1 in self.e2._extract([context]):
                    yield SpanPair(span0=(span0.promote()), span1=(span1.promote()))

class AlignedTableRelationExtractor(CandidateExtractor):
    """Table relation extraction for aligned cells only

    Axis argument can be:
        'row': output candidates aligned over rows
        'col': output candidates aligned over columns
         None: output candidates aligned over either rows or columns
    """
    def __init__(self, extractor1, extractor2, axis=None, join_key='context_id'):
        super(AlignedTableRelationExtractor, self).__init__(parallelism=False, join_key=join_key)
        self.axis = axis
        self.e1 = extractor1
        self.e2 = extractor2
        if axis not in ('row', 'col', None):
            raise Exception('Invalid axis type')

    def _extract(self, contexts):
        for context in contexts:
            for span0 in self.e1._extract([context]):
                for span1 in self.e2._extract([context]):
                    if self.axis == 'row':
                        if span0.context.cell.row_num != span1.context.cell.row_num: continue
                    if self.axis == 'col':
                        if span0.context.cell.col_num != span1.context.cell.col_num: continue
                    if self.axis is None:
                        if span0.context.cell.col_num != span1.context.cell.col_num \
                        and span0.context.cell.row_num != span1.context.cell.row_num: continue
                    yield SpanPair(span0=(span0.promote()), span1=(span1.promote()))                    


class Ngrams(CandidateSpace):
    """
    Defines the space of candidates as all n-grams (n <= n_max) in a Sentence _x_,
    indexing by **character offset**.
    """
    def __init__(self, n_max=5, split_tokens=['-', '/']):
        CandidateSpace.__init__(self)
        self.n_max = n_max
        self.split_rgx    = r'('+r'|'.join(split_tokens)+r')' if split_tokens and len(split_tokens) > 0 else None

    def apply(self, context):
        # Loop over all n-grams in **reverse** order (to facilitate longest-match semantics)
        L = len(context.char_offsets)
        for l in range(1, self.n_max+1)[::-1]:
            for i in range(L-l+1):
                # NOTE that we derive char_len without using sep
                char_start = context.char_offsets[i]
                cl = context.char_offsets[i+l-1] - context.char_offsets[i] + len(context.words[i+l-1])
                char_end = context.char_offsets[i] + cl - 1
                # if u"BC856" in context.words:
                #     import pdb; pdb.set_trace()
                yield TemporarySpan(char_start=char_start, char_end=char_end, context=context)

                # Check for split
                # NOTE: For simplicity, we only split single tokens right now!
                # if l == 1 and self.split_rgx is not None:
                #     m = re.search(self.split_rgx,
                #         context.text[char_start-context.char_offsets[0]:char_end-context.char_offsets[0]+1])
                #     if m is not None and l < self.n_max:
                #         if char_start > char_start + m.start(1) - 1 or char_start + m.end(1) > char_end:
                #             yield TemporarySpan(char_start=char_start, char_end=char_start + m.start(1) - 1, context=context)
                #             yield TemporarySpan(char_start=char_start + m.end(1), char_end=char_end, context=context)

class NgramsWithRanges(CandidateSpace):
    """
    Defines the space of candidates as all n-grams (n <= n_max) in a Phrase _x_,
    indexing by **character offset**. It also checks for n-grams that look like
    part number ranges and replaces them with the expanded contents.
    """
    def __init__(self, n_max=3):
        CandidateSpace.__init__(self)
        self.corenlp_handler = CoreNLPHandler()
        self.n_max = n_max

    def apply(self, context):
        # Loop over all n-grams in **reverse** order (to facilitate longest-match semantics)
        L = len(context.char_offsets)
        for l in range(1, self.n_max+1)[::-1]: # l = length of n-gram
            for i in range(L-l+1): # i = starting index
                # NOTE that we derive char_len without using sep
                char_start = context.char_offsets[i]
                cl = context.char_offsets[i+l-1] - context.char_offsets[i] + len(context.words[i+l-1])
                char_end = context.char_offsets[i] + cl - 1
                yield TemporarySpan(char_start=char_start, char_end=char_end, context=context)

                # look for ranges
                word_start = i
                word_end = i + l - 1
                text = context.words[word_start:word_end+1]
                expanded_texts = list(expand_implicit_text(u" ".join(text)))
                if len(expanded_texts) > 1:
                    expanded_text = " ".join(expanded_texts)
                    for parts in self.corenlp_handler.parse(context.document, expanded_text):
                        parts['document'] = context.document
                        parts['table'] = context.table
                        parts['cell'] = context.cell
                        if context.cell is not None:
                            parts['row_num'] = context.cell.row_num
                            parts['col_num'] = context.cell.col_num
                        parts['html_tag'] = context.html_tag
                        parts['html_attrs'] = context.html_attrs
                        parts['html_anc_tags'] = context.html_anc_tags
                        parts['html_anc_attrs'] = context.html_anc_attrs
                        p = Phrase(**parts)
                        yield TemporarySpan(char_start=0, char_end=(len(expanded_text)-1), context=p)
                

class TableNgrams(Ngrams):
    """
    Defines the space of candidates as all n-grams (n <= n_max) in a Table _x_,
    indexing by **character offset**.
    """
    def apply(self, context):
        try:
            phrases = context.phrases
        except:
            phrases = [context]

        for phrase in phrases:
            for temp_span in super(TableNgrams, self).apply(phrase):
                yield temp_span
