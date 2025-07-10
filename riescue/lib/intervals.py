class Intervals:
    def __init__(self, arg):
        self.link = list()
        if isinstance(arg, list):
            for i in arg:
                if isinstance(i, tuple):
                    self.add(i)
        elif isinstance(arg, tuple):
            self.add(arg)
        else:
            assert False, "Error"

    # SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
    # SPDX-License-Identifier: Apache-2.0

    def add(self, arg):
        self.link.append([arg[0], arg[1]])

    def len(self):
        return len(self.link)

    def __str__(self):
        str = ""
        for i in self.link:
            str += f"{i}, "

        return str

    def __iter__(self):
        for link in self.link:
            yield link

    def overlaps(self, d):
        a = self.link
        b = [[d[0], d[1]]]

        # ranges = []
        i = j = 0
        # while i < len(a) and j < len(b):
        while i < len(a):
            a_left, a_right = a[i]
            b_left, b_right = b[j]

            if a_right < b_right:
                i += 1
            else:
                j += 1

            if a_right >= b_left and b_right >= a_left:
                # end_pts = sorted([a_left, a_right, b_left, b_right])
                # middle = [end_pts[1], end_pts[2]]
                # ranges.append(middle)
                return True

        return False

        # ri = 0
        # while ri < len(ranges)-1:
        #     if ranges[ri][1] == ranges[ri+1][0]:
        #         ranges[ri:ri+2] = [[ranges[ri][0], ranges[ri+1][1]]]

        #     ri += 1

        # return ranges


# a = [[0, 2], [5, 10], [13, 23], [24, 25]]
# b = [[500, 1000]]
#
# intrvl1 = Intervals([(1,2), (3,4)])
# intrvl1.add((5,6))
# intrvl2 = Intervals((1,3))
#
# print(f'{intrvl1}')
# print(f'{intrvl2}')
#
# res = intrvl1.intersections((1,4))
# print(f'{res}')
#
# # v = intersections(a, b)
# # print(f'{v}')
