import argparse
from os import listdir
from os.path import isfile, join, splitext
from ntpath import basename
import numpy as np
import cv2
from matplotlib import pyplot as plt
from util import *
from path import *
import csv
# import multiprocessing
# from multiprocessing import Pool
from multiprocessing.pool import ThreadPool
from functools import partial

# Match road sign in image 

signs = {}
for f in listdir(SIGN_PATH):
    fn, ext = splitext(f)
    if isfile(join(SIGN_PATH, f)) and ext=='.png':
        signs[fn] = join(SIGN_PATH, f)

def match(img1, img2, **options):
    # defaults
    draw = False 
    matchColor = 'g' 
    singlePointColor = 'b' 
    match_flag = cv2.DRAW_MATCHES_FLAGS_NOT_DRAW_SINGLE_POINTS
    minMatchCnt = 10
    ratioTestPct = 0.7

    # options
    if 'draw' in options:
        draw = options['draw']
    if 'matchColor' in options:
        matchColor = options['matchColor']
    if 'singlePointColor' in options:
        singlePointColor = options['singlePointColor']
    if 'drawKeyPoint' in options:
        if options['drawKeyPoint']:
            match_flag = cv2.DRAW_MATCHES_FLAGS_DEFAULT
    if 'minMatchCnt' in options:
        minMatchCnt = options['minMatchCnt']
    if 'ratioTestPct' in options:
        ratioTestPct = options['ratioTestPct']

    draw_params = dict(matchColor = bgr(matchColor),
                       singlePointColor = bgr(singlePointColor),
                       flags = match_flag 
                       )

    # Initiate SIFT detector
    if iscv2():
        sift = cv2.SIFT()
    elif iscv3():
        sift = cv2.xfeatures2d.SIFT_create()
    
    # find the keypoints and descriptors with SIFT
    kp1, des1 = sift.detectAndCompute(img1,None)
    kp2, des2 = sift.detectAndCompute(img2,None)
    
    # FLANN parameters
    FLANN_INDEX_KDTREE = 0
    index_params = dict(algorithm = FLANN_INDEX_KDTREE, trees = 5)
    search_params = dict(checks=50)   # or pass empty dictionary
    
    flann = cv2.FlannBasedMatcher(index_params,search_params)
    
    matches = flann.knnMatch(des1,des2,k=2)
    
    # store all the good matches as per Lowe's ratio test.
    good = []
    for m,n in matches:
        if m.distance < ratioTestPct*n.distance:
            good.append(m)
        # good.append(m)

    matchdict = {}
    if len(good)>minMatchCnt:
        src_pts = np.float32([ kp1[m.queryIdx].pt for m in good ]).reshape(-1,1,2)
        dst_pts = np.float32([ kp2[m.trainIdx].pt for m in good ]).reshape(-1,1,2)

        M, mask = cv2.findHomography(src_pts, dst_pts, cv2.RANSAC, ransacReprojThreshold=1)
        draw_params['matchesMask'] = mask.ravel().tolist()

        if M is not None:
            # draw box around matched object
            if iscv3():
                lineType = cv2.LINE_AA
            elif iscv2():
                lineType = cv2.CV_AA
            h,w,_ = img1.shape
            cnrs = np.float32([ [0,0],[0,h-1],[w-1,h-1],[w-1,0]]).reshape(-1,1,2)
            tcnrs = cv2.perspectiveTransform(cnrs,M)
            tcnrs = np.int32(tcnrs)
            img2 = cv2.polylines(img=img2, pts=[tcnrs], isClosed=True, color=bgr('b'),
                    thickness=3, lineType=lineType)
            ctr = np.float32([[w/2, h/2]]).reshape(-1,1,2)
            tctr = tuple(np.int32(cv2.perspectiveTransform(ctr,M).flatten()))
            img2 = cv2.circle(img=img2, center=tctr, radius=2, color=bgr('r'), thickness=-1,
                    lineType=lineType)
            matchdict['cnrs'] = tcnrs.reshape(-1,2)
            matchdict['ctr'] = tctr

    else:
        # print "Not enough matches are found - %d/%d" % (len(good),MIN_MATCH_COUNT)
        draw_params['matchesMask'] = np.zeros(len(good))

    img3 = None
    if draw:
        img3 = cv2.drawMatches(img1,kp1,img2,kp2,good,None,**draw_params)
        img3 = cv2.cvtColor(img3, cv2.COLOR_BGR2RGB)
        return img3 
    else:
        return matchdict

def mcencode(mc):
    if len(mc)==0:
        return ''
    else:
        mcstr = []
        mcstr.append(','.join([str(c) for c in mc['ctr']]))
        for cnr in mc['cnrs']:
            mcstr.append(','.join([str(c) for c in cnr]))
        return ' . '.join(mcstr)

def mcdecode(mcstr):
    mc = {}
    if mcstr.strip()=='':
        return mc 
    cnrs = []
    for i, ms in enumerate(mcstr.split(' . ')):
        if i==0:
            mc['ctr'] = tuple([int(c) for c in ms.split(',')])
        else:
            cnrs.append(tuple([int(c) for c in ms.split(',')]))
    mc['cnrs'] = np.int32(cnrs)
    return mc

def mcwrite(matches, matchPath, **options):
    signNames = [sn for sn in signs]
    frames = [fn for fn in matches]
    with open('{0}matches.csv'.format(matchPath), 'w') as csvfile:
        writer = csv.writer(csvfile, delimiter=',')
        header = ['Frame'] + signNames 
        writer.writerow(header)
        for frame in frames:
            row = [frame]
            for sn in signNames:
                row.append(mcencode(matches[frame][sn]))
            writer.writerow(row)

def mcread(matchPath):
    matches = {}
    with open('{0}/matches.csv'.format(matchPath), 'r') as csvfile:
        reader = csv.DictReader(csvfile)
        for row in reader:
            fn = row['Frame']
            matches[fn] = {}
            for sn in row:
                if sn!='Frame':
                    matches[fn][sn] = mcdecode(row[sn])
    return matches

def matchFrame(fn, path, matches, blurSize, options):
    print('working on {0}'.format(fn))
    frame = cv2.imread(join(path, fn + '.png'))
    mcs = {}
    for signname in signs:
        sign = cv2.imread(signs[signname])
        sign = cv2.GaussianBlur(sign,blurSize,0)
        mc = match(sign, frame, **options)
        mcs[signname] = mc
    print('finishing on {0}'.format(fn))
    return mcs

def matchall(path, **options):
    startframe = 0
    endframe = -1
    numframe = -1
    matchPath = path
    blurSize = (5,5)
    numthread = 8
    options['draw'] = False

    if 'matchPath' in options:
        matchPath = options['matchPath']
    if 'blurSize' in options:
        blurSize = options['blurSize']
    if 'startframe' in options:
        startframe = options['startframe']
    if 'endframe' in options:
        endframe = options['endframe']
    if 'numframe' in options:
        numframe = options['numframe']
    if 'numthread' in options:
        numthread = options['numthread']

    files = [f for f in listdir(path) if isfile(join(path, f)) and f.endswith('.png')]
    files = sorted(files)

    matches = {}

    inputs = []
    for i, impath in enumerate(files): 
        if i<startframe:
            continue
        if endframe>0 and i>endframe:
            break
        if numframe>0 and i>(startframe + numframe):
            break

        fn, ext = splitext(impath)
        inputs.append(fn)
    inputs = np.array(inputs)

    pool = ThreadPool(numthread)
    partialMatchFrame = partial(matchFrame, matches=matches, path=path, blurSize=blurSize,
            options=options)
    # Test one 1 frame
    # partialMatchFrame(inputs[2])
    # return
    tic()
    results = pool.map(partialMatchFrame, inputs, 1)
    pool.close()
    pool.join()
    toc()
    for fn, mcs in zip(inputs, results):  
        matches[fn] = mcs

    mcwrite(matches, matchPath)

def main():
    usage = "Usage: match [options --mode]"
    parser = argparse.ArgumentParser(
        description='match a roadsign to an image or match allroadsigns to a video')
    parser.add_argument('--start-frame', dest='startframe', nargs='?', default=0, type=int,
            help='Starting frame to play')
    parser.add_argument('--end-frame', dest='endframe', nargs='?', default=-1, type=int,
            help='Ending frame to play, -1 for last frame')
    parser.add_argument('--num-frame', dest='numframe', nargs='?', default=-1, type=int,
            help='Number of frame to play, -1 for all frames')
    parser.add_argument('--mode', dest='mode', action='store', default='matchall')
    parser.add_argument('--ratioTestPct', dest='ratioTestPct', nargs='?', default=0.75, type=float,
            help='Ratio test percentage')
    parser.add_argument('--minMatchCnt', dest='minMatchCnt', nargs='?', default=5, type=int,
            help='Minimum match count')
    parser.add_argument('--numthread', dest='numthread', nargs='?', default=8, type=int,
            help='Number of thread to match roadsigns')
    parser.add_argument('--path', dest='path', action='store',
            default='{0}/2011_09_26_1/data/'.format(KITTI_PATH))
    (opts, args) = parser.parse_known_args()

    if (opts.mode == 'matchall'):
        options = dict(startframe=opts.startframe, numframe=opts.numframe,
                ratioTestPct=opts.ratioTestPct, minMatchCnt=opts.minMatchCnt,
                numthread=opts.numthread)
        matchall(opts.path, **options)
    elif (opts.mode == 'matchone'):
        # img1 = cv2.imread(signs['stop_sign'])
        # img2 = cv2.imread('{0}{1}'.format(DATA_PATH,
            # 'NRM_20160615005414_Goluk_T1_800865_png/NRM_20160615005414_Goluk_T1_800865_552.png'))
        img1 = cv2.imread(signs['pedestrian_crossing'])
        # img1 = cv2.imread(signs['parking'])
        img1 = cv2.GaussianBlur(img1,(5,5),0)
        img2 = cv2.imread(
            KITTI_PATH + 
            '/2011_09_26_1/data/0000000026.png'
            )
        img3 = match(img1, img2, draw=True, drawKeyPoint=False, ratioTestPct=0.75, minMatchCnt=5)
        plt.figure(dpi=140)
        plt.imshow(img3)
        plt.show()

if __name__ == "__main__":
    main()
