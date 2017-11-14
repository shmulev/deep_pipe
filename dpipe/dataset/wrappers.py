import functools
from typing import List
from collections import ChainMap

import numpy as np
import dpipe.medim as medim
from dpipe.config import register
from .base import Dataset


class Proxy:
    def __init__(self, shadowed):
        self._shadowed = shadowed

    def __getattr__(self, name):
        return getattr(self._shadowed, name)


register = functools.partial(register, module_type='dataset_wrapper')


@register()
def cached(dataset: Dataset) -> Dataset:
    n = len(dataset.patient_ids)

    class CachedDataset(Proxy):
        @functools.lru_cache(n)
        def load_mscan(self, patient_id):
            return self._shadowed.load_mscan(patient_id)

        @functools.lru_cache(n)
        def load_segm(self, patient_id):
            return self._shadowed.load_segm(patient_id)

        @functools.lru_cache(n)
        def load_msegm(self, patient_id):
            return self._shadowed.load_msegm(patient_id)

    return CachedDataset(dataset)


@register()
def apply_mask(dataset: Dataset, mask_modality_id: int = None,
               mask_value: int = None) -> Dataset:

    class MaskedDataset(Proxy):
        def load_mscan(self, patient_id):
            images = self._shadowed.load_mscan(patient_id)
            mask = images[mask_modality_id]
            mask_bin = (mask > 0 if mask_value is None else mask == mask_value)
            assert np.sum(mask_bin) > 0, 'The obtained mask is empty'
            images = [image * mask for image in images[:-1]]
            return np.array(images)

        @property
        def n_chans_mscan(self):
            return self._shadowed.n_chans_mscan - 1

    return dataset if mask_modality_id is None else MaskedDataset(dataset)


@register()
def bbox_extraction(dataset: Dataset) -> Dataset:
    # Use this small cache to speed up data loading. Usually users load
    # all scans for the same person at the same time
    load_mscan = functools.lru_cache(3)(dataset.load_mscan)

    class BBoxedDataset(Proxy):
        def load_mscan(self, patient_id):
            img = load_mscan(patient_id)
            mask = np.any(img > 0, axis=0)
            return medim.bb.extract([img], mask)[0]

        def load_segm(self, patient_id):
            img = self._shadowed.load_segm(patient_id)
            mask = np.any(load_mscan(patient_id) > 0, axis=0)
            return medim.bb.extract([img], mask=mask)[0]

        def load_msegm(self, patient_id):
            img = self._shadowed.load_msegm(patient_id)
            mask = np.any(load_mscan(patient_id) > 0, axis=0)
            return medim.bb.extract([img], mask=mask)[0]

    return BBoxedDataset(dataset)


@register()
def normalized(dataset: Dataset, mean, std,
               drop_percentile: int = None) -> Dataset:
    class NormalizedDataset(Proxy):
        def load_mscan(self, patient_id):
            img = self._shadowed.load_mscan(patient_id)
            return medim.prep.normalize_mscan(img, mean=mean, std=std,
                                              drop_percentile=drop_percentile)

    return NormalizedDataset(dataset)


@register()
def normalized_sub(dataset: Dataset) -> Dataset:
    class NormalizedDataset(Proxy):
        def load_mscan(self, patient_id):
            mscan = self._shadowed.load_mscan(patient_id)
            mask = np.any(mscan > 0, axis=0)
            mscan_inner = medim.bb.extract([mscan], mask)[0]

            mscan = mscan / mscan_inner.std(axis=(1, 2, 3), keepdims=True)

            return mscan

    return NormalizedDataset(dataset)


@register()
def add_groups_from_df(dataset: Dataset, group_col: str) -> Dataset:
    class GroupedFromMetadata(Proxy):
        @property
        def groups(self):
            return self._shadowed.df[group_col].as_matrix()

    return GroupedFromMetadata(dataset)


@register()
def add_groups_from_ids(dataset: Dataset, separator: str) -> Dataset:
    roots = [pi.split(separator)[0] for pi in dataset.patient_ids]
    root2group = dict(map(lambda x: (x[1], x[0]), enumerate(set(roots))))
    groups = tuple(root2group[pi.split(separator)[0]]
                   for pi in dataset.patient_ids)

    class GroupsFromIDs(Proxy):
        @property
        def groups(self):
            return groups

    return GroupsFromIDs(dataset)


@register()
def merge_datasets(datasets: List[Dataset]) -> Dataset:
    [np.testing.assert_array_equal(a.segm2msegm_matrix, b.segm2msegm_matrix)
     for a, b, in zip(datasets, datasets[1:])]

    assert all(dataset.n_chans_mscan == datasets[0].n_chans_mscan
               for dataset in datasets)

    patient_id2dataset = ChainMap(*({pi: dataset for pi in dataset.patient_ids}
                                    for dataset in datasets))

    patient_ids = sorted(list(patient_id2dataset.keys()))

    class MergedDataset(Proxy):
        @property
        def patient_ids(self):
            return patient_ids

        def load_mscan(self, patient_id):
            return patient_id2dataset[patient_id].load_mscan(patient_id)

        def load_segm(self, patient_id):
            return patient_id2dataset[patient_id].load_segm(patient_id)

        def load_msegm(self, patient_id):
            return patient_id2dataset[patient_id].load_msegm(patient_id)

    return MergedDataset(datasets[0])


@register()
def weighted(dataset: Dataset, thickness: str) -> Dataset:
    n = len(dataset.patient_ids)
    class WeightedBoundariesDataset(Proxy):
        @functools.lru_cache(n)
        def load_weighted_mask(self, patient_id) -> np.array:
            paths = self.df[thickness].loc[patient_id]
            image = self._load_by_paths(paths)

            return image

    return WeightedBoundariesDataset(dataset)
