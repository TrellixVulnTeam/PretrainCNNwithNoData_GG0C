# -*- coding: utf-8 -*-
from torchvision.datasets.vision import VisionDataset
from torch.utils.data import Dataset

from PIL import Image

import os,glob
import os.path
from typing import Any, Callable, cast, Dict, List, Optional, Tuple
import numpy as np
from PHdict import comp_PH, PH_hist, life_curve, comp_persistence_image, comp_landscape
from scipy.fft import fft2, ifft2
from skimage.filters import threshold_otsu


def generate_random_image(args):
    np.random.seed()
    sample=np.zeros(1)
    p = np.random.uniform(0,1)
    if p<args.prob_colour:
        channel = 3
    else:
        channel = 1
    while sample.max()-sample.min()<1e-10:
        sample = []
        for i in range(channel):
            alpha = np.random.uniform(*args.alpha_range)
            beta = np.random.uniform(*args.beta_range)
            x = np.linspace(1,np.exp(alpha)*args.img_size,args.img_size)
            X, Y = np.meshgrid(x,x)
            noise = np.random.uniform(0,1,(args.img_size,args.img_size))
            f = fft2(noise)
            f = f/(X**2+Y**2)**beta
            sample.append(ifft2(f).real)
        sample = np.array(sample)
    #print(sample.shape)
    #print(sample.min(), sample.max())
    for i in range(channel):
        p = np.random.uniform(0,1)
        if p<args.prob_binary/2:
            sample[i] = (sample[i] >= threshold_otsu(sample[i]))
        elif p<args.prob_binary:
            sample[i] = (sample[i] < threshold_otsu(sample[i]))
        else:
            sample[i] = (sample[i]-sample[i].min())/np.ptp(sample[i])
    sample = Image.fromarray((255*sample).astype(np.uint8).transpose(1,2,0).squeeze())
    return sample


class DatasetFolderPH(VisionDataset):
    def __init__(
            self,
            root: str,
            loader: Callable[[str], Any] = None,
            transform: Optional[Callable] = None,
            args = None
    ) -> None:
        super(DatasetFolderPH, self).__init__(root, transform=transform)

        self.args = args
        if root=="generate":
            self.generate_on_the_fly = True
            self.n_samples = args.n_samples
            self.classes = [0 for i in range(args.n_samples)]
            self.n_classes = 1
        else:
            self.generate_on_the_fly = False
            #self.samples = glob.glob(os.path.join(self.root, "**/*.png"), recursive=True)
            #self.samples.extend(glob.glob(os.path.join(self.root, "**/*.jpg"), recursive=True))
            self.samples, self.classes = make_dataset(self.root)
            self.n_samples = len(self.samples)
            self.n_classes = max(self.classes)+1

        if self.n_samples == 0:
            msg = "no files found in subfolders of: {}\n".format(self.root)
            raise RuntimeError(msg)

        if loader is None:
            self.loader = default_loader
        else:
            self.loader = loader

    def __getitem__(self, index: int) -> Tuple[Any, Any]:

        # input image generation/loading
        if self.generate_on_the_fly:
            sample=generate_random_image(self.args).convert('RGB')
        else:
            path = self.samples[index]
            sample = self.loader(path)

        # apply image transform; if path2PHdir is set, then PH will be loaded from file and thus the timing of transform does not matter.
        if self.args.persistence_after_transform and self.transform is not None:
            sample = self.transform(sample)

        if self.args.label_type_pt == "raw":
            hs = np.load(os.path.join(self.args.path2PHdir, os.path.splitext(os.path.basename(path))[0]+".npy")).astype(np.float32)
        else:
            # PH
            if self.args.path2PHdir == "on_the_fly":
                ph = comp_PH(np.array(sample),gradient=self.args.gradient, distance_transform=self.args.distance_transform)
            else:
                ph = np.load(os.path.join(self.args.path2PHdir, os.path.splitext(os.path.basename(path))[0]+".npy"))
            # PH vectorisation
            if self.args.label_type_pt == "life_curve":
                hs = life_curve(ph, self.args.num_bins, min_birth=self.args.min_birth, max_birth=self.args.max_birth, max_life=self.args.max_life).astype(np.float32)
            elif self.args.label_type_pt == "landscape":
                hs = comp_landscape(ph, self.args.num_bins, min_birth=self.args.min_birth, max_birth=self.args.max_birth, max_life=self.args.max_life, n=2).astype(np.float32)
            elif self.args.label_type_pt == "PH_hist":
                hs = PH_hist(ph, self.args.num_bins, min_birth=self.args.min_birth, max_birth=self.args.max_birth, max_life=self.args.max_life, bandwidth=self.args.bandwidth).astype(np.float32)
            elif self.args.label_type_pt == "persistence_image":
                hs = np.concatenate(comp_persistence_image(ph, self.args)).astype(np.float32)
            else:
                print("Unknown label type")
                exit()
    #        print(hs.shape, hs.max())

        # apply image transform
        if (not self.args.persistence_after_transform) and self.transform is not None:
            sample = self.transform(sample)

        # return values
        if self.args.learning_mode == "simultaneous":
            #onehot = np.zeros(self.n_classes).astype(np.float32)
            #onehot[self.classes[index]] = 1.0
            #target = np.concatenate([onehot, hs])
            target = [self.classes[index], hs]
        else:
            target = hs
        return sample, target

    def __len__(self) -> int:
        return self.n_samples


def pil_loader(path: str) -> Image.Image:
    # open path as file to avoid ResourceWarning (https://github.com/python-pillow/Pillow/issues/835)
    with open(path, 'rb') as f:
        img = Image.open(f)
        return img.convert('RGB')


# TODO: specify the return type
def accimage_loader(path: str) -> Any:
    import accimage
    try:
        return accimage.Image(path)
    except IOError:
        # Potentially a decoding problem, fall back to PIL.Image
        return pil_loader(path)


def default_loader(path: str) -> Any:
    from torchvision import get_image_backend
    if get_image_backend() == 'accimage':
        return accimage_loader(path)
    else:
        return pil_loader(path)


def find_classes(directory: str) -> Tuple[List[str], Dict[str, int]]:
    """Finds the class folders in a dataset.

    See :class:`DatasetFolder` for details.
    """
    classes = sorted(entry.name for entry in os.scandir(directory) if entry.is_dir())
    if not classes:
        #print("Couldn't find any class folder in ", directory)
        classes = ["."]
        class_to_idx = {".": 0}
    else:
        class_to_idx = {cls_name: i for i, cls_name in enumerate(classes)}
    return classes, class_to_idx

def has_file_allowed_extension(filename: str, extensions: Tuple[str, ...]) -> bool:
    return filename.lower().endswith(extensions)

def make_dataset(
    directory: str,
) -> List[Tuple[str, int]]:

    directory = os.path.expanduser(directory)
    _, class_to_idx = find_classes(directory)

    extensions = ('.jpg', '.jpeg', '.png', '.ppm', '.bmp', '.pgm', '.tif', '.tiff', '.webp')
    def is_valid_file(x: str) -> bool:
        return has_file_allowed_extension(x, cast(Tuple[str, ...], extensions))

    is_valid_file = cast(Callable[[str], bool], is_valid_file)

    paths = []
    classes = []
    available_classes = set()
    for target_class in sorted(class_to_idx.keys()):
        class_index = class_to_idx[target_class]
        target_dir = os.path.join(directory, target_class)
        if not os.path.isdir(target_dir):
            continue
        for root, _, fnames in sorted(os.walk(target_dir, followlinks=True)):
            for fname in sorted(fnames):
                path = os.path.join(root, fname)
                if is_valid_file(path):
                    paths.append(path)
                    classes.append(class_index)

                    if target_class not in available_classes:
                        available_classes.add(target_class)

    empty_classes = set(class_to_idx.keys()) - available_classes
    if empty_classes:
        msg = f"Found no valid file for the classes {', '.join(sorted(empty_classes))}. "
        if extensions is not None:
            msg += f"Supported extensions are: {', '.join(extensions)}"
        raise FileNotFoundError(msg)

    return paths,classes
