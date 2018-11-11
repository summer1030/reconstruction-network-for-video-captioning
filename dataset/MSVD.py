from __future__ import print_function, division

from collections import defaultdict
import os

import h5py
import pandas as pd
import torch
from torch.utils.data import Dataset, DataLoader
from torchvision import transforms

from dataset.transform import UniformSample, ZeroPadIfLessThan, ToTensor, RemovePunctuation, Lowercase, \
                              SplitWithWhiteSpace, Truncate, PadLast, PadToLength, ToIndex


class MSVD:
    """ MSVD DataLoader """
    
    def __init__(self, C):
        self.C = C
        self.vocab = None
        self.train_data_loader = None
        self.val_data_loader = None
        self.test_data_loader = None

        self.transform_sentence = transforms.Compose([
            RemovePunctuation(),
            Lowercase(),
            SplitWithWhiteSpace(),
            Truncate(self.C.caption_n_max_word),
        ])

        self.build()

    def build(self):
        self.build_vocab()
        self.build_data_loaders()

    def build_vocab(self):
        self.vocab = MSVDVocab(
            self.C.total_caption_fpath,
            self.C.init_word2idx,
            self.C.min_count,
            transform=self.transform_sentence)

    def collate_fn(self, batch):
        videos, captions = zip(*batch)

        videos = torch.stack(videos)
        captions = torch.stack(captions)

        # FIXME: Got error when dtype is diff with others
        videos = videos.float()
        captions = captions.float()

        """ (seq, batch, feature) """
        # videos = videos.transpose(0, 1)
        captions = captions.transpose(0, 1)

        """ Device """
        videos = videos.to(self.C.device)
        captions = captions.to(self.C.device)

        return videos, captions

    def build_data_loaders(self):
        """ Transformation """
        self.transform_frame = transforms.Compose([
            UniformSample(self.C.encoder_output_len),
            ZeroPadIfLessThan(self.C.encoder_output_len),
            ToTensor(torch.float),
        ])
        self.transform_caption = transforms.Compose([
            self.transform_sentence,
            ToIndex(self.vocab.word2idx),
            PadLast(self.vocab.word2idx['<EOS>']),
            PadToLength(self.vocab.word2idx['<PAD>'], self.vocab.max_sentence_len + 1),
            ToTensor(torch.long),
        ])

        if self.C.build_train_data_loader:
            self.train_data_loader = self.build_data_loader(self.C.train_video_fpath, self.C.train_caption_fpath)
        if self.C.build_val_data_loader:
            self.val_data_loader = self.build_data_loader(self.C.val_video_fpath, self.C.val_caption_fpath)
        if self.C.build_test_data_loader:
            self.test_data_loader = self.build_data_loader(self.C.test_video_fpath, self.C.test_caption_fpath)


    def build_data_loader(self, video_fpath, caption_fpath):
        dataset = MSVDDataset(
            video_fpath,
            caption_fpath,
            transform_frame=self.transform_frame,
            transform_caption=self.transform_caption)

        data_loader = DataLoader(
            dataset,
            batch_size=self.C.batch_size,
            shuffle=self.C.shuffle,
            num_workers=self.C.num_workers,
            collate_fn=self.collate_fn)

        return data_loader


class MSVDVocab:
    """ MSVD Vocaburary """

    def __init__(self, caption_fpath, init_word2idx, min_count=1, transform=str.split):
        self.caption_fpath = caption_fpath
        self.min_count = min_count
        self.transform = transform

        self.word2idx = init_word2idx
        self.idx2word = { v: k for k, v in self.word2idx.items() }
        self.word_freq_dict = defaultdict(lambda: 0)
        self.n_vocabs = len(self.word2idx)
        self.n_words = self.n_vocabs
        self.max_sentence_len = -1

        self.build()

    def load_captions(self):
        df = pd.read_csv(self.caption_fpath)
        df = df[df['Language'] == 'English']
        df = df[pd.notnull(df['Description'])]
        captions = df['Description'].values
        return captions

    def build(self):
        captions = self.load_captions()
        for caption in captions:
            words = self.transform(caption)
            self.max_sentence_len = max(self.max_sentence_len, len(words))
            for word in words:
                self.word_freq_dict[word] += 1
        self.n_vocabs_untrimmed = len(self.word_freq_dict)
        self.n_words_untrimmed = sum(list(self.word_freq_dict.values()))

        keep_words = [ word for word, freq in self.word_freq_dict.items() if freq >= self.min_count ]

        for idx, word in enumerate(keep_words, len(self.word2idx)):
            self.word2idx[word] = idx
            self.idx2word[idx] = word
        self.n_vocabs = len(self.word2idx)
        self.n_words = sum([ self.word_freq_dict[word] for word in keep_words ])


class MSVDDataset(Dataset):
    """ MSVD Dataset """

    def __init__(self, video_fpath, caption_fpath, transform_frame=None, transform_caption=None):
        self.video_fpath = video_fpath
        self.caption_fpath = caption_fpath
        self.transform_frame = transform_frame
        self.transform_caption = transform_caption

        self.build_video_caption_pairs()

    def __len__(self):
        return len(self.videos)

    def __getitem__(self, idx):
        video, caption = self.video_caption_pairs[idx]

        if self.transform_frame:
            video = self.transform_frame(video)
        if self.transform_caption:
            caption = self.transform_caption(caption)

        return video, caption
    
    def load_videos(self):
        fin = h5py.File(self.video_fpath, 'r')
        videos = {}
        for vid in fin:
            videos[vid] = fin[vid].value
        self.videos = videos
        return videos

    def load_captions(self):
        df = pd.read_csv(self.caption_fpath)
        df = df[df['Language'] == 'English']
        df = df[[ 'VideoID', 'Start', 'End', 'Description' ]]
        df = df[pd.notnull(df['Description'])]

        captions = defaultdict(lambda: [])
        for video_id, start, end, caption in df.values:
            vid = "{}_{}_{}".format(video_id, start, end)
            captions[vid].append(caption)
        self.captions = captions
        return captions

    def build_video_caption_pairs(self):
        self.load_videos()
        self.load_captions()

        self.video_caption_pairs = []
        for vid in self.videos:
            video = self.videos[vid]
            for caption in self.captions[vid]:
                self.video_caption_pairs.append(( video, caption ))
        return self.video_caption_pairs

