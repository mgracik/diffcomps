#!/usr/bin/env python

import argparse
from collections import defaultdict, namedtuple
import json
import logging
logging.basicConfig(level=logging.DEBUG)
from operator import itemgetter
import sys
import time
import xml.etree.ElementTree as ElementTree


Package = namedtuple('Package', 'name requires type')


class Comps(dict):

    def _parse_node(self, node):
        node_id = node.find('id').text
        node_data = {}

        names = {}
        for name in node.iter('name'):
            attrs = name.items()
            # XXX: No other attributes?
            lang = attrs[0][1] if attrs else None
            assert lang not in names
            names[lang] = name.text
        node_data['names'] = names

        descriptions = {}
        for desc in node.iter('description'):
            attrs = desc.items()
            # XXX: No other attributes?
            lang = attrs[0][1] if attrs else None
            assert lang not in descriptions
            descriptions[lang] = desc.text
        node_data['descriptions'] = descriptions

        return node_id, node_data

    def _parse(self, tag):
        start = time.clock()
        for node in self._xmlroot.iter(tag):
            node_id, node_data = self._parse_node(node)
            assert node_id not in self
            self[node_id] = node_data
        elapsed = time.clock() - start
        logging.debug("parsed '%s:%s' in %g seconds", self.filename, tag, elapsed)

    def __init__(self, xmlroot, filename):
        super(Comps, self).__init__()
        self._xmlroot = xmlroot
        self.filename = filename


class Groups(Comps):

    ATTRS = ('default', 'uservisible', 'langonly')

    def _parse_node(self, node):
        group_id, group_data = super(Groups, self)._parse_node(node)

        for tag in self.ATTRS:
            element = node.find(tag)
            if element is not None:
                group_data[tag] = element.text

        packagelist = node.find('packagelist')
        if packagelist is not None:
            packages = []
            for package in packagelist.iter('packagereq'):
                packages.append(Package(name=package.text,
                                        requires=package.get('requires'),
                                        type=package.get('type')))
            group_data['packages'] = packages

        return group_id, group_data

    def parse(self):
        self._parse(tag='group')

    @property
    def packages(self):
        if not hasattr(self, '_pkgacc'):
            self._pkgacc = defaultdict(set)
            for group_id in self:
                packages = self[group_id]['packages']
                for package in packages:
                    pkgtup = (group_id, package.requires, package.type)
                    self._pkgacc[package.name].add(pkgtup)
        return self._pkgacc


class Categories(Comps):

    ATTRS = ('display_order',)

    def _parse_node(self, node):
        category_id, category_data = super(Categories, self)._parse_node(node)

        for tag in self.ATTRS:
            element = node.find(tag)
            if element is not None:
                category_data[tag] = element.text

        grouplist = node.find('grouplist')
        if grouplist is not None:
            groups = []
            for group in grouplist.iter('groupid'):
                groups.append(group.text)
            category_data['groups'] = groups

        return category_id, category_data

    def parse(self):
        self._parse(tag='category')

    @property
    def groups(self):
        if not hasattr(self, '_grpacc'):
            self._grpacc = defaultdict(set)
            for category_id in self:
                groups = self[category_id]['groups']
                for group in groups:
                    self._grpacc[group].add(category_id)
        return self._grpacc


def parse_args(args=None):
    parser = argparse.ArgumentParser()
    parser.add_argument('-s', '--source', metavar='FILENAME', required=True)
    parser.add_argument('-t', '--target', metavar='FILENAME', required=True)
    return parser.parse_args(args) if args else parser.parse_args()


def parse_xml(filename):
    start = time.clock()
    tree = ElementTree.parse(filename)
    elapsed = time.clock() - start
    logging.debug('opened %s in %g seconds', filename, elapsed)
    return tree.getroot()


def diff_comps(source, target, attributes):

    def diff_dicts(source_dict, target_dict):
        additions = set((key, target_dict[key]) for key in target_dict
                        if key not in source_dict)
        removals, changes = set(), set()
        for key, value in source_dict.iteritems():
            if key not in target_dict:
                removals.add(key)
                continue
            if not value == target_dict[key]:
                changes.add((key, value, target_dict[key]))
        return additions, removals, changes

    diff = defaultdict(list)

    for node_id in target:
        if node_id not in source:
            diff[node_id].append('new')

    for node_id, source_data in source.iteritems():
        if node_id not in target:
            diff[node_id].append('removed')
            continue

        target_data = target[node_id]

        # Attributes.
        attr_dict = {}
        for attr in attributes:
            source_value = source_data.get(attr)
            target_value = target_data.get(attr)
            if source_value != target_value:
                attr_dict[attr] = target_value
        diff[node_id].append(attr_dict)

        # Names and descriptions.
        for tag in ('names', 'descriptions'):
            additions, removals, changes = diff_dicts(source_data[tag],
                                                      target_data[tag])
            new = map(itemgetter(0), additions | changes)
            diff[node_id].append({tag: {'new': sorted(new),
                                        'removed': sorted(removals)}})

    return diff


def diff_list(source, target):
    diff = defaultdict(list)

    for item, groups in target.iteritems():
        if item not in source:
            # New item.
            diff[item].append({'new': sorted(groups)})

    for item, groups in source.iteritems():
        if item not in target:
            # Completely removed item.
            diff[item].append({'removed':
                               sorted(map(itemgetter(0), groups))})
            continue

        # Compare.
        target_groups = target[item]
        if not (groups == target_groups):
            additions = target_groups - groups
            removals = groups - target_groups
            if additions:
                diff[item].append({'new': sorted(additions)})
            if removals:
                diff[item].append({'removed':
                                   sorted(map(itemgetter(0), removals))})

    return diff


if __name__ == '__main__':
    args = parse_args()
    source_xml = parse_xml(args.source)
    target_xml = parse_xml(args.target)

    source_groups = Groups(source_xml, args.source)
    source_groups.parse()
    target_groups = Groups(target_xml, args.target)
    target_groups.parse()

    source_categories = Categories(source_xml, args.source)
    source_categories.parse()
    target_categories = Categories(target_xml, args.target)
    target_categories.parse()

    start = time.clock()

    groups_diff = diff_comps(source_groups, target_groups, Groups.ATTRS)
    packages_diff = diff_list(source_groups.packages, target_groups.packages)
    categories_diff = diff_comps(source_categories, target_categories,
                                 Categories.ATTRS)
    grouplist_diff = diff_list(source_categories.groups,
                               target_categories.groups)

    elapsed = time.clock() - start
    logging.debug('diffed in %g seconds', elapsed)

    print json.dumps({'groups': groups_diff, 'packagelist': packages_diff,
                      'categories': categories_diff,
                      'grouplist': grouplist_diff},
                     indent=4, separators=(',', ': '))
