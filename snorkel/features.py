from collections import defaultdict
import scipy.sparse as sparse

from lxml import etree as et
from entity_features import get_ddlib_feats
from entity_features import compile_entity_feature_generator

class Featurizer(object):
    def __init__(self, candidates, corpus):
        self.num_cand = candidates.num_candidates()
        self._features_by_id = defaultdict(list)
        print "Extracting features..."
        self.feats = self.extract_features(candidates, corpus)
        print "Extracted {} features for each of {} candidates".format(self.num_features(), self.num_candidates())

    def num_candidates(self):
        return self.num_cand

    def num_features(self):
        return self.feats.shape[1]

    def extract_features(self, candidates, corpus):
        f_index = self._get_feature_index(candidates, corpus)
        f_matrix = self._get_feature_matrix(f_index)
        return f_matrix

    def _get_feature_index(self, candidates, corpus):
        f_index = defaultdict(list)
        for j,cand in enumerate(candidates):
            for feat in self._featurize(cand, corpus.get_context(cand.context_id)):
                self._features_by_id[cand.id].append(feat)
                f_index[feat].append(j)
        return f_index

    def _featurize(self):
        raise NotImplementedError

    def _get_feature_matrix(self, f_index):
        # Apply the feature generator, constructing a sparse matrix incrementally
        # Note that lil_matrix should be relatively efficient as we proceed row-wise
        self.feats = sparse.lil_matrix((self.num_candidates(), len(f_index)))
        for j,feat in enumerate(f_index.keys()):
            for i in f_index[feat]:
                self.feats[i,j] = 1
        return self.feats

    def get_features_by_id(self, id):
        features = self._features_by_id[id]
        return features if features is not None else None

    def get_features(self):
        return self.feats

class NgramFeaturizer(Featurizer):
    def _featurize(self, cand, context):
        # This is a poor man's substitue for coreNLP until they come together
        for feat in self.generate_temp_nlp_feats(cand, context):
            yield feat
        # for feat in self.generate_nlp_feats(cand, context):
        #     yield feat
        # for feat in self.generate_ddlib_feats(cand, context):
        #     yield feat

    def generate_temp_nlp_feats(self, cand, context):
        for ngram in cand.get_ngrams(cand.get_attrib_tokens('words')):
            yield ''.join(["BASIC_NGRAM_", ngram])

    # def generate_nlp_feats(self, cand, context):
    #     get_nlp_feats = compile_entity_feature_generator()
    #     for feat in get_nlp_feats(cand.root, cand.idxs):
    #         yield ''.join(["NLP_", feat])

    # def generate_ddlib_feats(self, cand, context):
    #     for feat in get_ddlib_feats(cand, cand.idxs):
    #         yield ''.join(["DDLIB_", feat])

class TableNgramFeaturizer(NgramFeaturizer):
    def _featurize(self, cand, context):
        for feat in super(TableNgramFeaturizer, self)._featurize(cand, context):
            yield feat
        for feat in self.generate_table_feats(cand, context):
            yield ''.join(["TABLE_",feat])

    def generate_table_feats(self, cand, context):
        yield "ROW_NUM_%s" % cand.row_num
        yield "COL_NUM_%s" % cand.col_num
        yield "HTML_TAG_" + cand.html_tag
        for attr in cand.html_attrs:
            yield "HTML_ATTR_" + attr
        for tag in cand.html_anc_tags:
            yield "HTML_ANC_TAG_" + tag
        for attr in cand.html_anc_attrs:
            yield "HTML_ANC_ATTR_" + attr
        for ngram in cand.get_aligned_ngrams(context, axis='row'):
            yield "ROW_NGRAM_" + ngram
        for ngram in cand.get_aligned_ngrams(context, axis='col'):
            yield "COL_NGRAM_" + ngram
