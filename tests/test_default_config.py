'''chromaterm.default_config tests'''
import os

import chromaterm.default_config


def assert_matches(positives, negatives, rule, match_count=1):
    '''Assert that all positives are matched while negatives are not.'''
    def permutate_data(data):
        output = []

        for entry in data if isinstance(data, list) else [data]:
            entry = entry.encode()

            output.append(entry)  # Plain entry
            output.append(b'hello ' + entry)  # Start changed
            output.append(entry + b' world')  # End changed
            output.append(b'hello ' + entry + b' world')  # Both changed

        return output

    for data in permutate_data(positives):
        assert len(rule.get_matches(data)) == match_count

    for data in permutate_data(negatives):
        assert len(rule.get_matches(data)) == 0


def test_rule_numbers():
    '''Default rule: Numbers.'''
    positives = ['1', '123', '123.123']
    negatives = ['1.1.', '.1.1', '1.2.3']
    rule = chromaterm.default_config.RULE_NUMBERS

    assert_matches(positives, negatives, rule)


def test_rule_ipv4():
    '''Default rule: IPv4 addresses.'''
    positives = ['192.168.2.1', '255.255.255.255', '=240.3.2.1', '1.2.3.4/32']
    negatives = ['256.255.255.255', '1.2.3']
    rule = chromaterm.default_config.RULE_IPV4

    assert_matches(positives, negatives, rule)


def test_rule_ipv6():
    '''Default rule: IPv6 addresses.'''
    positives = [
        'A:b:3:4:5:6:7:8', 'A::', 'A:b:3:4:5:6:7::', 'A::8', '::b:3:4:5:6:7:8',
        '::8', 'A:b:3:4:5:6::8', 'A:b:3:4:5::7:8', 'A:b:3:4::6:7:8', '::',
        'A:b:3::5:6:7:8', 'A:b::4:5:6:7:8', 'A::3:4:5:6:7:8', 'A::7:8', 'A::8',
        'A:b:3:4:5::8', 'A::6:7:8', 'A:b:3:4::8', 'A::5:6:7:8', 'A:b:3::8',
        'A::4:5:6:7:8', 'A:b::8', 'A:b:3:4:5:6:7:8/64', '::255.255.255.255',
        '::ffff:255.255.255.255', 'fe80::1%tun', '::ffff:0:255.255.255.255',
        '00A:db8:3:4::192.0.2.33', '64:ff9b::192.0.2.33', ':::'
    ]
    negatives = [
        '1:2', '1:2:3', '1:2:3:4', '1:2:3:4:5', '1:2:3:4:5:6:7', ':::1'
        '1:2:3:4:5:6:7:8:9', '1:2:3:4:5:6:7::8', 'abcd:xyz::1', 'fe80:1%tun'
    ]
    rule = chromaterm.default_config.RULE_IPV6

    assert_matches(positives, negatives, rule)


def test_rule_mac():
    '''Default rule: MAC addresses.'''
    positives = ['0A:23:45:67:89:AB', '0a:23:45:67:89:ab', '0a23.4567.89ab']
    negatives = [
        '0A:23:45:67:89', '0A:23:45:67:89:AB:', '0A23.4567.89.AB',
        '0a23.4567.89ab.', '0a:23:45:67:xy:zx', '0a23.4567.xyzx'
    ]
    rule = chromaterm.default_config.RULE_MAC

    assert_matches(positives, negatives, rule)


def test_rule_size():
    '''Default rule: Size.'''
    positives = [
        '100b', '1kb', '1kib', '1Kb', '1kbps', '1.1kb', '1mb', '1gb', '1tb',
        '1pb', '1eb', '1zb', '1yb'
    ]
    negatives = ['100', '1kg', '1kic', '1kbpm', '1kibb', '1.1.kb', '100wb']
    rule = chromaterm.default_config.RULE_SIZE

    assert_matches(positives, negatives, rule, match_count=2)


def test_rule_date():
    '''Default rule: Date.'''
    positives = [
        '2019-12-31', '2019-12-31', 'jan 2019', 'feb 2019', 'Mar 2019',
        'apr 2019', 'MAY 2019', 'Jun 2019', 'jul  2019', 'AUG 19', 'sep 20',
        'oct 21', 'dec 23', 'AUG 19 2021', '24 jan', '25 feb 2019'
    ]
    negatives = [
        '201-12-31', '2019-13-31', '2019-12-32', 'xyz 2019', 'Jun 201',
        'xyz 26', 'jun 32', '32 jun'
    ]
    rule = chromaterm.default_config.RULE_DATE

    assert_matches(positives, negatives, rule)


def test_rule_time():
    '''Default rule: Time.'''
    positives = ['23:59', '23:01', '23:01:01', '23:01:01.123', '23:01:01-0800']
    negatives = ['24:59', '23:60', '23:1', '23:01:1', '23:01:01:', '23:01.123']
    rule = chromaterm.default_config.RULE_TIME

    assert_matches(positives, negatives, rule)


def test_write_default_config():
    '''Write config file.'''
    path = __name__ + '1'
    assert chromaterm.default_config.write_default_config(path)
    assert os.access(path, os.F_OK)
    os.remove(path)


def test_write_default_config_exists():
    '''Config file already exists.'''
    path = __name__ + '2'
    with open(path, 'w', encoding='utf-8'):
        assert not chromaterm.default_config.write_default_config(path)
    os.remove(path)


def test_write_default_config_no_permission():
    '''No write permission on directory.'''
    path = __name__ + '3' + '/hi.yml'
    os.mkdir(os.path.dirname(path), mode=0o444)
    assert not chromaterm.default_config.write_default_config(path + '/hi')
    os.rmdir(os.path.dirname(path))
