from lightgbm import LGBMRegressor
from lightgbm import Booster
from tqdm import tqdm
import numpy as np

#https://staff.fnwi.uva.nl/e.kanoulas/wp-content/uploads/Lecture-8-1-LambdaMart-Demystified.pdf
#http://citeseerx.ist.psu.edu/viewdoc/download?doi=10.1.1.180.634&rep=rep1&type=pdf

class LambdaMART:
    class QueriesGroup:
        def __init__(self, assessor, is_test=False):
            # if evaluation - no need to update scores 
            self.is_test = is_test
            # number of docs in group
            self.count_docs = len(assessor)
            if self.is_test:
                # if eval fill with ones 
                self.make_step(np.ones((self.count_docs, self.count_docs)))
            else:
                self.assessor = np.array(assessor, dtype=np.uint8)
                
                # max dcg to normalize ndcg 
                self.best_score = np.sum((2.0 ** np.sort(self.assessor)[::-1] - 1) / np.log2(np.arange(2, self.count_docs+2)))
                # zero division
                if self.best_score == 0:
                    self.best_score = 1.0

                # table with indexes for convience
                self.permutations = np.tile(np.arange(0, self.count_docs), (self.count_docs, 1))
                self.make_step(np.zeros((self.count_docs, )))

        def make_step(self, new_scores):
            """ Performs calculation 
                compute the Newton step
            """
            SIGMA = 1

            self.scores = np.array(new_scores, dtype = np.int32)
            # sorted indexes of new positions
            self.positions = np.zeros((self.count_docs, ), dtype=np.int16)
            self.positions[np.argsort(new_scores)[::-1]] = np.arange(1, self.count_docs + 1)

            if not self.is_test:
                # Z - NDCG (might be other utility difference generated by swapping the rank positions)
                Z = ((((2 ** self.assessor.reshape(-1, 1)) - 1) - ((2 ** self.assessor[self.permutations]) - 1)) \
                * (-1.0 / np.log2(self.positions.reshape(-1, 1)+1) + 1.0 / np.log2(self.positions[self.permutations]+1))) \
                 / self.best_score

                # in group count permutations  
                lambda_ij = ((self.assessor.reshape((-1, 1))  # correct
                                    > self.assessor[self.permutations]).astype(np.uint8))
                lambda_ji = ((self.assessor.reshape((-1, 1))  # not correct 
                                        < self.assessor[self.permutations]).astype(np.uint8))

                # 1/(e^σ(s_i-s_j))
                self.p_ij = 1.0 / (1 + np.exp(SIGMA *  np.abs((self.scores.reshape((-1, 1)) - self.scores[self.permutations]))))
                
                # gradient (dC)/(ds_i)
                self.gradients = -np.sum(np.abs(Z) * self.p_ij * ( lambda_ij - lambda_ji), axis=1)
                # hessian (d^2C)/(ds_i^2)
                self.hessians = np.sum(SIGMA * SIGMA * np.abs(Z) * self.p_ij * (1.0 - self.p_ij) * (lambda_ij + lambda_ji), axis=1)

    def __obj_func(self):
        tree = 0
        def func(y_true, y_pred):
            nonlocal tree
            print("Fitting tree #" + str(tree) + " ...")
            tree+=1

            for query, indexes in enumerate(self.doc_indexes):
                self.queries[query].make_step(y_pred[indexes])

            grads = np.concatenate([query.gradients   for query in self.queries])
            hess  = np.concatenate([query.hessians    for query in self.queries])

            return grads, hess

        return func

    def __data_processing(self, X, y, qid, is_test):
        print("Preprocessing data ...")
        self.X = X
        self.y = y
        self.queries = []
        self.doc_indexes = []
        # grop by qid
        self.groups = np.unique(qid)
        for query in tqdm(self.groups):
            # For each query add num of groups to doc_index
            self.doc_indexes.append(np.where(qid == query)[0])

            #new_query = LambdaMART.QueriesGroup(self.y[self.doc_indexes[-1]], is_test)
            self.queries.append(LambdaMART.QueriesGroup(self.y[self.doc_indexes[-1]], is_test))

    def __init__(self, **kwargs):
        self.params = kwargs
        self.clf = None

    def fit(self, X, y ,qid):
        self.__data_processing(X, y, qid, False)
        print("Fit ...")
        if self.clf == None:
            self.clf = LGBMRegressor(objective = self.__obj_func(), **self.params)
        self.clf.fit(X, y)

    def predict(self, X, qid):
        results = self.clf.predict(X)
        self.__data_processing(X, np.array(X.shape[0] * [-1]), qid, True)
        print('Predict ...')
        for query, indexes in enumerate(self.doc_indexes) :
            self.queries[query].make_step(results[indexes])

        results = []
        # because docs indexes are in direct order
        # we need to add base to each query
        base_index = 0
        for query_index, _ in enumerate(self.groups):
            doc_pos = self.queries[query_index].positions

            query_pred = np.full((len(doc_pos), ), base_index)
            query_pred[doc_pos-1] += np.arange(1, len(doc_pos)+1)

            for pos in query_pred:
                results.append(pos)

            base_index += len(self.queries[query_index].positions)

        return results

    def save(self, fname):
        self.clf.booster_.save_model(fname)

    def load(self, fname):
        self.clf = Booster(model_file=fname)
